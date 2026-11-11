# Batch-only application of whole-SoC clock and external interface assumptions.
# The caller freezes files under SOC_CONSTRAINTS_CASE and limits this process.
# No compile, STA acceptance or program simulation is performed in this step.
proc soc_constraints_progress {phase} {
    puts "SOC_CONSTRAINTS_PHASE=$phase"
    flush stdout
}

proc soc_constraints_select {design} {
    if {[sizeof_collection $design] != 1} {error "Expected one exact design object"}
    redirect -variable context_message {current_design $design}
    if {[compare_collections [current_design] $design] != 0} {
        error "Could not select the exact intended design object"
    }
}

proc soc_constraints_unwrap_list {value} {
    # A single object's list-valued attribute may be wrapped in another list.
    while {[llength $value] == 1} {
        set next [lindex $value 0]
        if {$next eq $value} {break}
        set value $next
    }
    return $value
}

proc soc_constraints_write_records {path records} {
    set stream [open $path w]
    foreach record [lsort $records] {puts $stream [join $record "\t"]}
    close $stream
}

proc soc_constraints_ports {path} {
    set result {}
    foreach_in_collection port [get_ports -quiet *] {
        lappend result [list [get_object_name $port] [get_attribute $port direction]]
    }
    set result [lsort $result]
    soc_constraints_write_records $path $result
    return $result
}

proc soc_constraints_pe_pins {path} {
    set result {}
    set cell [get_cells -quiet u_pe]
    if {[sizeof_collection $cell] != 1} {error "Expected the single real u_pe instance"}
    foreach_in_collection pin [get_pins -quiet -of_objects $cell] {
        set nets [lsort [get_object_name [get_nets -quiet -of_objects $pin]]]
        if {[llength $nets] != 1} {error "Expected one connected net for every PE interface bit"}
        lappend result [list [get_object_name $pin] [get_attribute $pin direction] $nets]
    }
    if {[llength $result] != 256} {error "Expected 256 PE interface bit pins"}
    set result [lsort $result]
    soc_constraints_write_records $path $result
    return $result
}

proc soc_constraints_main {} {
    if {![info exists ::env(SOC_CONSTRAINTS_CASE)]} {
        error "SOC_CONSTRAINTS_CASE is required"
    }
    set case_dir [file normalize $::env(SOC_CONSTRAINTS_CASE)]
    set input_dir [file join $case_dir inputs]
    set output_dir [file join $case_dir dc]
    file mkdir $output_dir
    source [file join $case_dir expected.tcl]
    foreach {name fallback} {
        expected_top soc_top expected_top_inputs 154 expected_data_inputs 152
        expected_output_bits 224 expected_unmapped_count 181160
        expected_pe_count 256 expected_sram_count 256
        expected_wrapper_top sram_multiplier_system_ID_WIDTH3
    } {
        if {![info exists $name]} {set $name $fallback}
    }
    set soc_ddc [file join $input_dir soc_elaborated.ddc]
    set standard_db [file join $input_dir standard.db]
    set sram_db [file join $input_dir sram.db]
    set sdc_file [file join $input_dir soc_io.sdc]
    set input_files [list $soc_ddc $standard_db $sram_db $sdc_file \
        [file join $case_dir expected.tcl] [file normalize [info script]]]
    set input_hashes [eval exec sha256sum -- $input_files]

    soc_constraints_progress load_soc
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list * $standard_db $sram_db gtech.db]
    redirect -file [file join $output_dir libraries.log] {
        read_db $standard_db
        read_db $sram_db
    }
    if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
        [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
        error "Expected the frozen standard and SRAM libraries"
    }
    # Saved local link files must not be consulted by implicit query linking
    # while their imported per-design attributes are being replaced below.
    set_app_var auto_link_disable true
    redirect -file [file join $output_dir load.log] {
        set loaded_designs [read_ddc $soc_ddc]
        set_app_var auto_link_disable true
    }
    if {[lsearch -exact $loaded_designs $expected_top] < 0} {
        error "The input DDC does not contain the expected SoC top"
    }
    # read_ddc may restore its former linking environment.
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list * $standard_db $sram_db gtech.db]
    set soc_design [get_designs -quiet -exact "${soc_ddc}:$expected_top"]
    # Every saved design can carry its own former local link file. Override
    # those attributes before any hierarchy lookup can reload an older DDC.
    # Clearing the specific link attribute preserves clocks, protection and
    # all other attributes; reset_design would remove required protection.
    set imported_designs ""
    set imported_objects {}
    foreach name $loaded_designs {
        set object [get_designs -quiet -exact "${soc_ddc}:$name"]
        if {[sizeof_collection $object] != 1} {
            error "Cannot uniquely resolve frozen imported design $name"
        }
        set imported_designs [add_to_collection $imported_designs $object]
        lappend imported_objects $object
    }
    soc_constraints_progress clear_obsolete_local_link_files
    foreach object $imported_objects {
        if {[soc_constraints_unwrap_list [get_attribute -quiet $object local_link_library]] ne ""} {
            remove_attribute $object local_link_library
            if {[soc_constraints_unwrap_list [get_attribute -quiet $object local_link_library]] ne ""} {
                error "Cannot clear an imported design's obsolete local link file"
            }
        }
    }
    soc_constraints_select $soc_design
    # All design definitions already came from the one frozen DDC. Resolve
    # hierarchy from those exact in-memory objects and frozen technology libs;
    # do not add another DDC file that could supply duplicate definitions.
    set_app_var auto_link_disable false
    redirect -file [file join $output_dir link.log] {set linked [link]}
    if {!$linked} {error "Cannot link the saved SoC"}
    if {[compare_collections [add_to_collection [get_designs -hierarchical *] $soc_design] \
            $imported_designs] != 0} {
        error "The SoC hierarchy resolved to a design outside the frozen imported DDC"
    }
    if {[get_object_name [current_design]] ne $expected_top ||
        [sizeof_collection [all_inputs]] != $expected_top_inputs ||
        [sizeof_collection [all_outputs]] != $expected_output_bits} {
        error "Unexpected SoC top or external input/output bit interface"
    }
    set data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]
    if {[sizeof_collection $data_inputs] != $expected_data_inputs ||
        [sizeof_collection [get_ports -quiet {clk rst_n}]] != 2} {
        error "Unexpected external data-input, clock or reset interface"
    }
    if {[sizeof_collection [get_clocks -quiet *]] != 0} {
        error "Expected the saved SoC without old block or system clocks"
    }
    set pe_cell [get_cells -quiet u_pe]
    if {[sizeof_collection $pe_cell] != 1 ||
        [get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [get_attribute -quiet $pe_cell dont_touch] ne "true"} {
        error "Expected the protected real mapped accelerator at u_pe"
    }
    set wrapper_design [get_designs -hierarchical -quiet -exact $expected_wrapper_top]
    if {[sizeof_collection $wrapper_design] != 1 ||
        ![get_attribute $wrapper_design is_mapped]} {
        error "The reused accelerator is not completely mapped"
    }
    set unmapped_before [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    set leaves_before [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false"]]
    if {[get_attribute $soc_design is_mapped] || $unmapped_before != $expected_unmapped_count} {
        error "Expected the known unmapped CPU/router logic before compilation"
    }
    set pe_count [sizeof_collection [get_cells -hierarchical \
        -filter "ref_name =~ *pe_1sram_4mul"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != $expected_pe_count || $sram_count != $expected_sram_count ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected the original PE count and SRAMs bound to their real library"
    }
    soc_constraints_select $wrapper_design
    set wrapper_unmapped [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    set array_cell [get_cells -quiet u_pe_array]
    if {$wrapper_unmapped != 0 || [sizeof_collection $array_cell] != 1} {
        error "Unexpected unmapped logic or missing array inside the reused accelerator"
    }
    set array_reference [get_attribute $array_cell ref_name]
    if {[info exists expected_array_reference] && $array_reference ne $expected_array_reference} {
        error "The accelerator contains a different array reference"
    }
    soc_constraints_select $soc_design
    set ports_before [soc_constraints_ports [file join $output_dir top_ports_before.tsv]]
    set pins_before [soc_constraints_pe_pins [file join $output_dir pe_pins_before.tsv]]
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "Whole-SoC constraints expect verified ns/fF units"
    }

    soc_constraints_progress apply_clock_and_interfaces
    source $sdc_file
    set core_clock [get_clocks -quiet core_clk]
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection $core_clock] != 1 ||
        [get_attribute $core_clock period] != 10.0} {
        error "Expected one core clock with the 10 ns, 50 percent waveform"
    }
    set clock_waveform [soc_constraints_unwrap_list [get_attribute $core_clock waveform]]
    if {[llength $clock_waveform] != 2 || [lindex $clock_waveform 0] != 0.0 ||
        [lindex $clock_waveform 1] != 5.0} {
        error "Unexpected core clock waveform"
    }
    soc_constraints_write_records [file join $output_dir clock_attributes.tsv] [list \
        [list clock_name [get_object_name $core_clock]] \
        [list period_ns [get_attribute $core_clock period]] \
        [list waveform_rise_ns [lindex $clock_waveform 0]] \
        [list waveform_fall_ns [lindex $clock_waveform 1]]]
    soc_constraints_progress report_ports
    redirect -file [file join $output_dir units.rpt] {report_units}
    redirect -file [file join $output_dir clocks.rpt] {
        report_clock -attributes -nosplit
        report_clock -skew -nosplit
    }
    redirect -file [file join $output_dir ports.rpt] {
        report_port -verbose -nosplit -significant_digits 6
    }
    set ports_after [soc_constraints_ports [file join $output_dir top_ports_after.tsv]]
    set pins_after [soc_constraints_pe_pins [file join $output_dir pe_pins_after.tsv]]
    if {$ports_before ne $ports_after || $pins_before ne $pins_after ||
        [compare_collections [current_design] $soc_design] != 0 ||
        [get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [get_attribute -quiet $pe_cell dont_touch] ne "true" ||
        [sizeof_collection [get_cells -hierarchical \
            -filter "is_hierarchical == false"]] != $leaves_before ||
        [sizeof_collection [get_cells -hierarchical \
            -filter "is_hierarchical == false && is_unmapped == true"]] != $unmapped_before} {
        error "Design identity, interface, connections or leaf mapping changed while applying constraints"
    }
    soc_constraints_progress save
    redirect -file [file join $output_dir save.log] {
        if {![write_sdc [file join $output_dir soc_constraints.sdc]] ||
            ![write -format ddc -hierarchy -output [file join $output_dir soc_constrained.ddc]] ||
            ![write -format verilog -hierarchy -output [file join $output_dir soc_constrained.v]]} {
            error "Cannot save the constrained SoC and actual constraints"
        }
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An input changed while applying the whole-SoC constraints"
    }
    set stream [open [file join $output_dir constraint_summary.rpt] w]
    foreach {key value} [list \
        top $expected_top input_bits $expected_top_inputs data_input_bits $expected_data_inputs \
        output_bits $expected_output_bits clock_name core_clk clock_count 1 \
        period_ns 10 uncertainty_ns 0.1 clock_transition_ns 0.005 \
        input_min_ns 0 input_max_ns 1 input_transition_ns 0.02 \
        reset_reference falling_edge reset_min_ns 0 reset_max_ns 0 \
        output_min_ns 0 output_max_ns 1 output_pin_load_ff 5 \
        pe_instances $pe_count sram_instances $sram_count array_reference $array_reference \
        mapped_pe_reference $expected_wrapper_top pe_protected true \
        leaf_objects $leaves_before cpu_router_unmapped_leaf_cells $unmapped_before \
        pe_wrapper_unmapped_leaf_cells $wrapper_unmapped \
        imported_designs [llength $loaded_designs] obsolete_local_links_cleared true \
        scoped_in_memory_linking true \
        interface_and_pe_connections_preserved true soc_mapped false \
        compile_performed false sta_acceptance_established false simulation_performed false \
        formal_equivalence_checked false] {
        puts $stream "$key=$value"
    }
    close $stream
    puts "DC_SOC_CONSTRAINTS_APPLIED period_ns=10 data_input_bits=$expected_data_inputs output_bits=$expected_output_bits"
    flush stdout
}

if {[catch {soc_constraints_main} soc_constraints_error]} {
    puts stderr "SOC_CONSTRAINTS_FAILED: $soc_constraints_error"
    if {[info exists ::errorInfo]} {puts stderr $::errorInfo}
    exit 1
}
exit 0
