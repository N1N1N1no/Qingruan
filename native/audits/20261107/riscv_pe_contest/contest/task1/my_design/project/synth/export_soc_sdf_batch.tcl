# Private subprocess entry: export the already mapped SoC, without synthesis.
proc soc_export_progress {phase} {
    puts "SOC_SDF_EXPORT_PHASE=$phase"
    flush stdout
}
proc soc_export_select {design} {
    if {[sizeof_collection $design] != 1} {error "Expected one exact design object"}
    redirect -variable message {current_design $design}
    if {[compare_collections [current_design] $design] != 0} {
        error "Could not select the exact intended design object"
    }
}
proc soc_export_unwrap {value} {
    while {[llength $value] == 1} {
        set next [lindex $value 0]
        if {$next eq $value} {break}
        set value $next
    }
    return $value
}
proc soc_export_records {path records} {
    set stream [open $path w]
    foreach record [lsort $records] {puts $stream [join $record "\t"]}
    close $stream
}
proc soc_export_ports {path} {
    set records {}
    foreach_in_collection port [get_ports -quiet *] {
        lappend records [list [get_object_name $port] [get_attribute $port direction]]
    }
    soc_export_records $path $records
}
proc soc_export_pins {path} {
    set records {}
    set cell [get_cells -quiet u_pe]
    if {[sizeof_collection $cell] != 1} {error "Expected the real u_pe instance"}
    foreach_in_collection pin [get_pins -quiet -of_objects $cell] {
        lappend records [list [get_object_name $pin] [get_attribute $pin direction] \
            [lsort [get_object_name [get_nets -quiet -of_objects $pin]]]]
    }
    if {[llength $records] != 256} {error "Expected 256 accelerator bit pins"}
    soc_export_records $path $records
}
proc soc_export_main {} {
    if {![info exists ::env(SOC_SDF_EXPORT_CASE)]} {error "SOC_SDF_EXPORT_CASE is required"}
    set case_dir [file normalize $::env(SOC_SDF_EXPORT_CASE)]
    set input_dir [file join $case_dir inputs]
    set output_dir [file join $case_dir dc]
    source [file join $case_dir expected.tcl]
    foreach name {expected_top expected_top_inputs expected_data_inputs expected_output_bits
        expected_pe_count expected_sram_count expected_wrapper_top expected_array_reference
        expected_wrapper_fingerprint expected_array_fingerprint expected_design_count} {
        if {![info exists $name]} {error "Missing frozen expectation: $name"}
    }
    set soc_ddc [file join $input_dir soc_mapped.ddc]
    set standard_db [file join $input_dir standard.db]
    set sram_db [file join $input_dir sram.db]
    set input_files [list $soc_ddc $standard_db $sram_db \
        [file join $input_dir soc_mapped.v] [file join $input_dir soc_constraints.sdc] \
        [file join $input_dir pe_axi_fingerprint.tcl] [file join $case_dir expected.tcl] \
        [file normalize [info script]]]
    set input_hashes [eval exec sha256sum -- $input_files]
    source [file join $input_dir pe_axi_fingerprint.tcl]
    set_host_options -max_cores 1
    redirect -file [file join $output_dir host_options.rpt] {report_host_options}
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list * $standard_db $sram_db gtech.db]
    soc_export_progress load_libraries
    redirect -file [file join $output_dir libraries.log] {
        read_db $standard_db
        read_db $sram_db
    }
    if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
        [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
        error "Expected the exact frozen standard and SRAM libraries"
    }
    soc_export_progress load_mapped_soc
    set_app_var auto_link_disable true
    redirect -file [file join $output_dir load.log] {
        set loaded_designs [read_ddc $soc_ddc]
        set_app_var auto_link_disable true
    }
    if {[llength $loaded_designs] != $expected_design_count ||
        [lsearch -exact $loaded_designs $expected_top] < 0} {
        error "The frozen DDC has an unexpected set of imported designs"
    }
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list * $standard_db $sram_db gtech.db]
    set imported_designs ""
    set imported_objects {}
    foreach name $loaded_designs {
        set object [get_designs -quiet -exact "${soc_ddc}:$name"]
        if {[sizeof_collection $object] != 1} {error "Cannot uniquely resolve imported $name"}
        set imported_designs [add_to_collection $imported_designs $object]
        lappend imported_objects $object
    }
    set soc_design [get_designs -quiet -exact "${soc_ddc}:$expected_top"]
    soc_export_progress clear_obsolete_local_links
    foreach object $imported_objects {
        if {[soc_export_unwrap [get_attribute -quiet $object local_link_library]] ne ""} {
            remove_attribute $object local_link_library
            if {[soc_export_unwrap [get_attribute -quiet $object local_link_library]] ne ""} {
                error "Cannot clear an imported design's obsolete local link files"
            }
        }
    }
    soc_export_select $soc_design
    set_app_var auto_link_disable false
    redirect -file [file join $output_dir link.log] {set linked [link]}
    if {!$linked} {error "Cannot link frozen mapped SoC"}
    if {[compare_collections [add_to_collection [get_designs -hierarchical *] $soc_design] \
            $imported_designs] != 0} {
        error "Reachable hierarchy is not exactly the frozen imported DDC"
    }
    if {[get_object_name [current_design]] ne $expected_top ||
        ![get_attribute $soc_design is_mapped] ||
        [sizeof_collection [all_inputs]] != $expected_top_inputs ||
        [sizeof_collection [all_outputs]] != $expected_output_bits ||
        [sizeof_collection [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]] != $expected_data_inputs} {
        error "Unexpected mapped whole-SoC interface"
    }
    set unmapped [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    set pe_count [sizeof_collection [get_cells -hierarchical -filter "ref_name =~ *pe_1sram_4mul"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$unmapped != 0 || $pe_count != $expected_pe_count || $sram_count != $expected_sram_count ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Mapped logic, PE count or SRAM library binding differs"
    }
    set core_clock [get_clocks -quiet core_clk]
    if {[sizeof_collection [get_clocks -quiet *]] != 1 || [sizeof_collection $core_clock] != 1 ||
        [get_attribute $core_clock period] != 10.0} {error "Expected the saved 10 ns core_clk"}
    set waveform [soc_export_unwrap [get_attribute $core_clock waveform]]
    if {[llength $waveform] != 2 || [lindex $waveform 0] != 0.0 || [lindex $waveform 1] != 5.0} {
        error "Unexpected saved clock waveform"
    }
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {error "Expected ns/fF units"}
    soc_export_ports [file join $output_dir top_ports.tsv]
    soc_export_pins [file join $output_dir pe_pins.tsv]
    set pe_cell [get_cells -quiet u_pe]
    set wrapper_design [get_designs -hierarchical -quiet -exact $expected_wrapper_top]
    if {[sizeof_collection $pe_cell] != 1 || [get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [get_attribute -quiet $pe_cell dont_touch] ne "true" || [sizeof_collection $wrapper_design] != 1 ||
        [get_attribute -quiet $wrapper_design dont_touch] ne "true" ||
        ![get_attribute $wrapper_design is_mapped]} {error "Mapped protected accelerator differs"}
    soc_export_select $wrapper_design
    set array_cell [get_cells -quiet u_pe_array]
    set array_design [get_designs -hierarchical -quiet -exact $expected_array_reference]
    if {[sizeof_collection $array_cell] != 1 || [get_attribute $array_cell ref_name] ne $expected_array_reference ||
        [sizeof_collection $array_design] != 1 || ![get_attribute $array_design is_mapped]} {
        error "Mapped array identity differs"
    }
    soc_export_select $soc_design
    soc_export_progress verify_accelerator_fingerprints
    set wrapper_hash [pe_axi_array_fingerprint $wrapper_design [file join $output_dir pe_fingerprint.txt]]
    set array_hash [pe_axi_array_fingerprint $array_design [file join $output_dir array_fingerprint.txt]]
    if {$wrapper_hash ne $expected_wrapper_fingerprint || $array_hash ne $expected_array_fingerprint} {
        error "The protected accelerator's structure differs"
    }
    redirect -file [file join $output_dir check_design.rpt] {set design_ok [check_design]}
    if {!$design_ok} {error "Mapped SoC check_design failed"}
    redirect -file [file join $output_dir ports.rpt] {report_port -verbose -nosplit -significant_digits 6}
    redirect -file [file join $output_dir clocks.rpt] {
        report_clock -attributes -nosplit
        report_clock -skew -nosplit
    }
    redirect -file [file join $output_dir units.rpt] {report_units}
    soc_export_progress save_matching_netlist_constraints
    redirect -file [file join $output_dir save.log] {
        if {![write_sdc [file join $output_dir soc_constraints.sdc]] ||
            ![write -format ddc -hierarchy -output [file join $output_dir soc_mapped.ddc]] ||
            ![write -format verilog -hierarchy -output [file join $output_dir soc_mapped.v]]} {
            error "Cannot save matching mapped design and actual constraints"
        }
    }
    soc_export_progress write_sdf
    set sdf_file [file join $output_dir soc_mapped.sdf]
    redirect -tee -file [file join $output_dir sdf.log] {
        write_sdf -version 2.1 -significant_digits 6 $sdf_file
    }
    if {![file exists $sdf_file] || [file size $sdf_file] == 0} {error "No SDF was written"}
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {error "An export input changed"}
    set stream [open [file join $output_dir export_summary.rpt] w]
    foreach {key value} [list top $expected_top input_bits $expected_top_inputs \
        data_input_bits $expected_data_inputs output_bits $expected_output_bits \
        imported_designs [llength $loaded_designs] reachable_designs [sizeof_collection $imported_designs] \
        unmapped_leaf_cells $unmapped pe_instances $pe_count sram_instances $sram_count \
        mapped_pe_reference $expected_wrapper_top array_reference $expected_array_reference \
        mapped_pe_fingerprint $wrapper_hash array_fingerprint $array_hash \
        period_ns 10 max_cpu_cores 1 check_design_success true \
        scoped_in_memory_linking true compile_performed false sdf_exported true \
        sdf_version 2.1 sdf_significant_digits 6 simulation_performed false \
        physical_parasitics_used false formal_equivalence_checked false] {
        puts $stream "$key=$value"
    }
    close $stream
    puts "DC_SOC_SDF_EXPORTED top=soc_top pe_instances=$pe_count sram_instances=$sram_count"
    flush stdout
}
if {[catch {soc_export_main} export_error]} {
    puts stderr "SOC_SDF_EXPORT_FAILED: $export_error"
    if {[info exists ::errorInfo]} {puts stderr $::errorInfo}
    exit 1
}
exit 0
