# Batch-only structural import. The caller freezes all files in SOC_READ_CASE.
# No compile, timing constraints, or simulation is performed here.
proc soc_read_progress {phase} {
    puts "SOC_READ_PHASE=$phase"
    flush stdout
}

proc soc_read_select {design} {
    if {[sizeof_collection $design] != 1} {
        error "Expected one design object"
    }
    redirect -variable context_message {current_design $design}
    if {[compare_collections [current_design] $design] != 0} {
        error "Could not select the exact requested design object"
    }
}

proc soc_read_direction {direction} {
    switch -- $direction {
        input {return in}
        output {return out}
        in {return in}
        out {return out}
        default {error "Unsupported port direction: $direction"}
    }
}

proc soc_read_expected_ports {ports} {
    set result {}
    foreach item $ports {
        if {[llength $item] != 2} {
            error "Expected each port signature entry to contain a name and direction"
        }
        lappend result [list [lindex $item 0] [soc_read_direction [lindex $item 1]]]
    }
    if {[llength $result] != [llength [lsort -unique $result]]} {
        error "Duplicate expected bit port signature"
    }
    return [lsort $result]
}

proc soc_read_port_signature {} {
    set result {}
    foreach_in_collection port [get_ports -quiet *] {
        lappend result [list [get_object_name $port] \
            [soc_read_direction [get_attribute $port direction]]]
    }
    return [lsort $result]
}

proc soc_read_write_records {path records} {
    set stream [open $path w]
    foreach record $records {
        puts $stream [join $record "\t"]
    }
    close $stream
}

proc soc_read_pin_snapshot {cell expected_ports path} {
    set result {}
    set ports {}
    set prefix "[get_object_name $cell]/"
    foreach_in_collection pin [get_pins -quiet -of_objects $cell] {
        set name [get_object_name $pin]
        if {[string first $prefix $name] != 0} {
            error "Unexpected pin outside the intended PE instance: $name"
        }
        set name [string range $name [string length $prefix] end]
        set direction [soc_read_direction [get_attribute $pin direction]]
        set nets [lsort [get_object_name [get_nets -quiet -of_objects $pin]]]
        if {[llength $nets] != 1} {
            error "PE pin $name does not have exactly one connected top-level net"
        }
        lappend ports [list $name $direction]
        lappend result [list $name $direction $nets]
    }
    if {[lsort $ports] ne $expected_ports} {
        error "The original or replaced PE instance has an unexpected bit interface"
    }
    set result [lsort $result]
    soc_read_write_records $path $result
    return $result
}

proc soc_read_main {} {
    if {![info exists ::env(SOC_READ_CASE)]} {
        error "SOC_READ_CASE is required for this batch-only entry"
    }
    set case_dir [file normalize $::env(SOC_READ_CASE)]
    set input_dir [file join $case_dir inputs]
    set output_dir [file join $case_dir dc]
    file mkdir $output_dir
    source [file join $case_dir expected.tcl]
    set expected_top_ports [soc_read_expected_ports $expected_top_ports]
    set expected_pe_ports [soc_read_expected_ports $expected_pe_ports]
    if {![info exists expected_wrapper_top]} {
        set expected_wrapper_top sram_multiplier_system_ID_WIDTH3
    }
    if {![info exists expected_pe_count]} {set expected_pe_count 256}
    if {![info exists expected_sram_count]} {set expected_sram_count 256}
    if {![info exists expected_reused_physical_leaf_cells]} {
        set expected_reused_physical_leaf_cells 804927
    }
    if {![info exists expected_reused_virtual_constants]} {
        set expected_reused_virtual_constants 3586
    }
    set pe_ddc [file join $input_dir pe_axi_mapped.ddc]
    set standard_db [file join $input_dir standard.db]
    set sram_db [file join $input_dir sram.db]
    set control_verilog [file join $case_dir prepared soc_control_dc.v]
    set boundary_stub [file join $case_dir prepared pe_boundary_stub.v]
    set input_files [list $pe_ddc $standard_db $sram_db $control_verilog $boundary_stub \
        [file join $input_dir pe_axi_fingerprint.tcl] \
        [file join $case_dir expected.tcl] [file normalize [info script]]]
    set input_hashes [eval exec sha256sum -- $input_files]
    source [file join $input_dir pe_axi_fingerprint.tcl]

    soc_read_progress load_mapped_pe
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list $pe_ddc $standard_db $sram_db gtech.db]
    redirect -file [file join $output_dir libraries.log] {
        read_db $standard_db
        read_db $sram_db
    }
    if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
        [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
        error "The frozen standard and SRAM libraries did not load"
    }
    redirect -file [file join $output_dir pe_load.log] {
        set loaded_designs [read_ddc $pe_ddc]
    }
    if {[lsearch -exact $loaded_designs $expected_wrapper_top] < 0} {
        error "The frozen DDC did not contain the expected mapped AXI/PE top"
    }
    # Saved DDC environments must not redirect linking to their original paths.
    set_app_var link_library [list $pe_ddc $standard_db $sram_db gtech.db]
    set imported_designs ""
    set imported_objects {}
    foreach name $loaded_designs {
        set object [get_designs -quiet -exact "${pe_ddc}:$name"]
        if {[sizeof_collection $object] != 1} {
            error "Cannot uniquely resolve the frozen imported design $name"
        }
        set imported_designs [add_to_collection $imported_designs $object]
        lappend imported_objects $object
    }
    set wrapper_design [get_designs -quiet -exact "${pe_ddc}:$expected_wrapper_top"]
    soc_read_progress clear_previous_boundary_constraints
    close [open [file join $output_dir clear_pe_constraints.log] w]
    foreach design $imported_objects {
        redirect -append -file [file join $output_dir clear_pe_constraints.log] {
            soc_read_select $design
            if {![reset_design]} {error "Cannot clear an imported design's old constraints"}
            set_local_link_library [list $pe_ddc]
        }
    }
    soc_read_select $wrapper_design
    redirect -file [file join $output_dir pe_link.log] {set linked [link]}
    if {!$linked || ![get_attribute $wrapper_design is_mapped] ||
        [sizeof_collection [get_cells -hierarchical -filter \
            "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "The imported AXI/PE hierarchy is not completely mapped and linked"
    }
    if {[soc_read_port_signature] ne $expected_pe_ports} {
        error "Mapped AXI/PE interface differs from the specialized original u_pe"
    }
    set reused_leaf_count [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false"]]
    set reused_constant_count [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && (ref_name == **logic_0** || ref_name == **logic_1**)"]]
    if {$reused_leaf_count != $expected_reused_physical_leaf_cells + $expected_reused_virtual_constants ||
        $reused_constant_count != $expected_reused_virtual_constants} {
        error "Unexpected mapped AXI/PE leaf or virtual-constant counts"
    }
    set array_cell [get_cells -quiet u_pe_array]
    if {[sizeof_collection $array_cell] != 1} {error "Missing original PE array"}
    set array_reference [get_attribute $array_cell ref_name]
    if {[info exists expected_array_reference] && $array_reference ne $expected_array_reference} {
        error "Unexpected original mapped array reference"
    }
    set array_design [get_designs -hierarchical -quiet -exact $array_reference]
    soc_read_progress fingerprint_mapped_pe_before
    set wrapper_before [pe_axi_array_fingerprint $wrapper_design \
        [file join $output_dir pe_before.txt]]
    set array_before [pe_axi_array_fingerprint $array_design \
        [file join $output_dir array_before.txt]]
    if {[info exists expected_array_fingerprint] && $array_before ne $expected_array_fingerprint} {
        error "The imported mapped array does not match its export fingerprint"
    }

    soc_read_progress read_cpu_and_router
    # The preparer retains only the real u_pe boundary as a temporary HDL stub.
    # Its implementation is replaced explicitly below, never used as a final box.
    redirect -file [file join $output_dir control_read.log] {
        set boundary_designs [read_verilog -rtl $boundary_stub]
        if {[llength $boundary_designs] != 1} {
            error "Cannot read the specialized temporary PE boundary interface"
        }
        set control_designs [read_verilog -rtl $control_verilog]
    }
    if {[lsearch -exact $control_designs soc_top] < 0} {
        error "Cannot read the prepared CPU/router control netlist"
    }
    current_design soc_top
    set soc_design [current_design]
    if {[get_object_name $soc_design] ne "soc_top"} {error "Expected soc_top"}
    reset_design
    set_local_link_library [list $pe_ddc]
    set_app_var link_library [list $pe_ddc $standard_db $sram_db gtech.db]
    if {[soc_read_port_signature] ne $expected_top_ports} {
        error "The imported SoC top bit interface differs from the frozen original"
    }
    soc_read_write_records [file join $output_dir top_ports.tsv] [soc_read_port_signature]
    set pe_cell [get_cells -quiet u_pe]
    if {[sizeof_collection $pe_cell] != 1} {error "Expected exactly one original u_pe instance"}
    set boundary_reference [get_attribute $pe_cell ref_name]
    if {$boundary_reference eq $expected_wrapper_top} {
        error "The temporary PE boundary unexpectedly bypassed the explicit replacement"
    }
    set pins_before [soc_read_pin_snapshot $pe_cell $expected_pe_ports \
        [file join $output_dir u_pe_pins_before.tsv]]

    soc_read_progress bind_mapped_pe
    if {![change_link $pe_cell "${pe_ddc}:$expected_wrapper_top"]} {
        error "Cannot explicitly bind u_pe to the frozen mapped AXI/PE design"
    }
    redirect -file [file join $output_dir soc_link.log] {set linked [link]}
    if {!$linked} {error "The CPU/router and mapped AXI/PE system did not link"}
    set pe_cell [get_cells -quiet u_pe]
    if {[get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [compare_collections [get_designs -hierarchical -quiet -exact $expected_wrapper_top] $wrapper_design] != 0} {
        error "The SoC selected a different mapped AXI/PE implementation"
    }
    set pins_after [soc_read_pin_snapshot $pe_cell $expected_pe_ports \
        [file join $output_dir u_pe_pins_after.tsv]]
    if {$pins_before ne $pins_after} {
        error "Replacing the temporary PE boundary changed its external bit connections"
    }
    set_dont_touch $pe_cell true
    set soc_unmapped [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {$soc_unmapped == 0 || [get_attribute $soc_design is_mapped]} {
        error "Expected real, still-unmapped CPU/router logic around the preserved PE system"
    }
    set pe_count [sizeof_collection [get_cells -hierarchical \
        -filter "ref_name =~ *pe_1sram_4mul"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != $expected_pe_count || $sram_count != $expected_sram_count ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "The integrated SoC lost PE instances or bound SRAM library instances"
    }
    # Every imported definition must still be the one reachable from soc_top.
    set soc_hierarchy [get_designs -hierarchical *]
    if {[sizeof_collection [get_designs -hierarchical -quiet -exact $boundary_reference]] != 0} {
        error "The empty temporary PE boundary remains in the final SoC hierarchy"
    }
    foreach design $imported_objects {
        if {[sizeof_collection [remove_from_collection $design $soc_hierarchy]] != 0} {
            error "An imported mapped definition is no longer in the SoC hierarchy"
        }
    }
    soc_read_progress fingerprint_mapped_pe_after
    set wrapper_after [pe_axi_array_fingerprint $wrapper_design \
        [file join $output_dir pe_after.txt]]
    set array_after [pe_axi_array_fingerprint $array_design \
        [file join $output_dir array_after.txt]]
    if {$wrapper_before ne $wrapper_after || $array_before ne $array_after} {
        error "The reused mapped AXI/PE structure changed while integrating the CPU/router"
    }
    soc_read_select $wrapper_design
    if {![get_attribute $wrapper_design is_mapped] ||
        [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]] != $reused_leaf_count ||
        [sizeof_collection [get_cells -hierarchical -filter \
            "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "The reused mapped AXI/PE implementation changed its mapping state"
    }
    soc_read_select $soc_design
    if {[sizeof_collection [get_clocks -quiet *]] != 0} {
        error "Old block clocks remain at the new SoC boundary"
    }
    soc_read_progress check_and_save
    redirect -file [file join $output_dir check_design.rpt] {set design_ok [check_design]}
    if {!$design_ok} {error "Integrated SoC structural check_design failed"}
    redirect -file [file join $output_dir save.log] {
        if {![write -format ddc -hierarchy -output [file join $output_dir soc_elaborated.ddc]] ||
            ![write -format verilog -hierarchy -output [file join $output_dir soc_elaborated.v]]} {
            error "Could not save the integrated structural design"
        }
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An input changed during the structural import"
    }
    set stream [open [file join $output_dir read_summary.rpt] w]
    foreach {key value} [list \
        top soc_top cpu_and_router_included true \
        mapped_pe_reference $expected_wrapper_top \
        pe_instances $pe_count sram_instances $sram_count \
        imported_mapped_designs [llength $loaded_designs] \
        reused_mapped_leaf_objects $reused_leaf_count \
        reused_physical_leaf_cells $expected_reused_physical_leaf_cells \
        reused_virtual_constant_leaf_cells $reused_constant_count \
        reused_unmapped_leaf_cells 0 \
        cpu_router_unmapped_leaf_cells $soc_unmapped \
        top_port_bits [llength $expected_top_ports] \
        pe_interface_bits [llength $expected_pe_ports] \
        pe_pin_connections_preserved true \
        mapped_pe_fingerprint_before $wrapper_before \
        mapped_pe_fingerprint_after $wrapper_after \
        array_fingerprint_before $array_before \
        array_fingerprint_after $array_after \
        check_design_success true clock_count 0 \
        soc_mapped false compile_performed false \
        sta_performed false simulation_performed false \
        formal_equivalence_checked false] {
        puts $stream "$key=$value"
    }
    close $stream
    puts "DC_READ_SOC_CONTROL_PASS top=soc_top cpu_router_unmapped=$soc_unmapped pe_instances=$pe_count sram_instances=$sram_count"
    flush stdout
}

if {[catch {soc_read_main} soc_read_error]} {
    puts stderr "SOC_CONTROL_READ_FAILED: $soc_read_error"
    if {[info exists ::errorInfo]} {puts stderr $::errorInfo}
    exit 1
}
exit 0
