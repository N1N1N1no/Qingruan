# Batch-only mapping of the CPU/router around the already mapped accelerator.
# The caller freezes files under SOC_COMPILE_CASE and bounds this process.
# A synthesis DONE marker does not establish timing acceptance or equivalence.
proc soc_compile_progress {phase} {
    puts "SOC_COMPILE_PHASE=$phase"
    flush stdout
}

proc soc_compile_select {design} {
    if {[sizeof_collection $design] != 1} {error "Expected one exact design object"}
    redirect -variable context_message {current_design $design}
    if {[compare_collections [current_design] $design] != 0} {
        error "Could not select the exact intended design object"
    }
}

proc soc_compile_unwrap_list {value} {
    while {[llength $value] == 1} {
        set next [lindex $value 0]
        if {$next eq $value} {break}
        set value $next
    }
    return $value
}

proc soc_compile_write_records {path records} {
    set stream [open $path w]
    foreach record [lsort $records] {puts $stream [join $record "\t"]}
    close $stream
}

proc soc_compile_ports {path} {
    set result {}
    foreach_in_collection port [get_ports -quiet *] {
        lappend result [list [get_object_name $port] [get_attribute $port direction]]
    }
    set result [lsort $result]
    soc_compile_write_records $path $result
    return $result
}

proc soc_compile_pe_pins {path} {
    set result {}
    set signature {}
    set diagnostics {}
    set statuses {}
    set failures {}
    set cell [get_cells -quiet u_pe]
    if {[sizeof_collection $cell] != 1} {error "Expected the single real u_pe instance"}
    foreach_in_collection pin [get_pins -quiet -of_objects $cell] {
        set name [get_object_name $pin]
        set direction [get_attribute $pin direction]
        # Use only APIs verified in this installed DC. Its get_nets has no
        # -all switch; tie nets and constant_value were tested separately.
        set nets [lsort [get_object_name [get_nets -quiet -of_objects $pin]]]
        set connected [lsort [get_object_name [all_connected $pin]]]
        set constant_value [soc_compile_unwrap_list [get_attribute -quiet $pin constant_value]]
        set net_count [llength $nets]
        lappend diagnostics [list $name $direction $net_count $nets $constant_value \
            [llength $connected] $connected]
        set status connected_net
        if {$net_count > 1} {
            set status invalid_multiple_nets
            lappend failures [list pin $name direction $direction net_count $net_count \
                nets $nets constant_value $constant_value all_connected $connected]
        } elseif {$net_count == 0} {
            if {$direction eq "in" || $direction eq "input"} {
                if {$constant_value eq "0" || $constant_value eq "1"} {
                    set status "constant_input_$constant_value"
                    puts "SOC_PE_INTERFACE_CONSTANT_INPUT pin=$name value=$constant_value"
                } else {
                    set status invalid_unconnected_input
                    lappend failures [list pin $name direction $direction net_count $net_count \
                        nets $nets constant_value $constant_value all_connected $connected]
                }
            } elseif {$direction eq "out" || $direction eq "output"} {
                # An unused output may lose its external net during mapping.
                # Preserve and identify that fact; check_design and subsequent
                # independent netlist/program verification remain required.
                set status unconnected_output
                puts "SOC_PE_INTERFACE_UNCONNECTED_OUTPUT pin=$name"
            } else {
                set status invalid_unconnected_direction
                lappend failures [list pin $name direction $direction net_count $net_count \
                    nets $nets constant_value $constant_value all_connected $connected]
            }
        }
        lappend statuses [list $name $direction $status]
        lappend signature [list $name $direction]
        lappend result [list $name $direction $nets]
    }
    # Save every pin before reporting any failure. A rejected checkpoint
    # therefore retains the exact failing names, direction, counts and nets.
    soc_compile_write_records $path $result
    set detail_path "[file rootname $path]_net_counts.tsv"
    set stream [open $detail_path w]
    puts $stream "pin\tdirection\tnet_count\tnets\tconstant_value\tall_connected_count\tall_connected_names"
    foreach record [lsort $diagnostics] {puts $stream [join $record "\t"]}
    close $stream
    set status_path "[file rootname $path]_connectivity.tsv"
    set stream [open $status_path w]
    puts $stream "pin\tdirection\tconnectivity"
    foreach record [lsort $statuses] {puts $stream [join $record "\t"]}
    close $stream
    if {[llength $result] != 256} {
        error "Expected 256 PE interface bit pins, found [llength $result]; inspect $path"
    }
    if {[llength $failures] != 0} {
        error "Invalid accelerator interface connectivity; failures=$failures; inspect $detail_path and $status_path"
    }
    # The compiler may legally rename/buffer the CPU-side nets. Preserve the
    # bit interface; record connections for review rather than requiring old
    # top-level net names after logic mapping.
    return [lsort $signature]
}

proc soc_compile_clock {} {
    set core_clock [get_clocks -quiet core_clk]
    if {[sizeof_collection [get_clocks -quiet *]] != 1 ||
        [sizeof_collection $core_clock] != 1 ||
        [get_attribute $core_clock period] != 10.0} {
        error "Expected the saved single 10 ns core_clk"
    }
    set waveform [soc_compile_unwrap_list [get_attribute $core_clock waveform]]
    if {[llength $waveform] != 2 || [lindex $waveform 0] != 0.0 ||
        [lindex $waveform 1] != 5.0} {
        error "Unexpected saved core clock waveform"
    }
    return $core_clock
}

# Keep the diagnosed libraries exact and record constrained-checkpoint provenance.
# Re-saving equivalent DDCs can change bytes through paths/metadata, so that hash
# alone does not accept/reject logic. The exact post-compile cell/ref/pin/function
# guards remain mandatory; changed mappings fail there. Fresh full STA and strict
# SDF/program regression are still required after every successful resize.
proc soc_compile_sdf_compatibility_inputs {input_dir output_dir} {
    set stream [open [file join $output_dir sdf_compatibility_inputs.rpt] w]
    puts $stream "recipe=soc_nor_negative_iopath_v1"
    puts $stream "scope=library_identity_and_constrained_checkpoint_provenance_only"
    foreach {name expected} {
        soc_constrained.ddc c5a2f5f43aa423ae84fd4363bcdea5cac3431109ef017dcbecfbed65f9b29ac7
        standard.db 5a2542bd7b2c56bfc7c53373a9e02fc8ee5d57cf5bb069a4d64b3a9a883bb02a
        sram.db 97d9966ea52ea9e63b3e9dfa5ae896a171017670d26e508e040e5a92795475f6
    } {
        set actual [lindex [exec sha256sum -- [file join $input_dir $name]] 0]
        puts $stream "$name expected=$expected actual=$actual"
        if {$name eq "soc_constrained.ddc"} {
            puts $stream "constrained_checkpoint_bytes_match_diagnosis=[expr {$actual eq $expected}]"
            puts $stream "mapping_acceptance_requires=post_compile_exact_cell_ref_pins_library_function_and_full_regression"
        } elseif {$actual ne $expected} {
            puts $stream "status=FAIL"
            close $stream
            error "SDF compatibility recipe input changed: $name; diagnose the new mapping before updating the recipe"
        }
    }
    puts $stream "status=PASS"
    close $stream
}

proc soc_compile_sdf_compatibility_pins {cell} {
    set records {}
    set pins [get_pins -quiet -of_objects $cell]
    if {[sizeof_collection $pins] != 3} {error "Expected exactly three pins on U103023"}
    foreach_in_collection pin $pins {
        set direction [get_attribute $pin direction]
        if {$direction eq "input"} {set direction in}
        if {$direction eq "output"} {set direction out}
        set nets [get_nets -quiet -of_objects $pin]
        if {[sizeof_collection $nets] != 1} {
            error "Expected one connected net on [get_object_name $pin]"
        }
        lappend records [list [get_object_name $pin] $direction [get_object_name $nets]]
    }
    return [lsort $records]
}

# NOR2_X2 A1->ZN fall was -0.000780732 ns in the diagnosed synthesis corner.
# Resizing this equivalent gate to X1 made that arc +0.00362659 ns. This changes
# the actual mapped circuit; it never edits SDF/model values or timing budgets.
proc soc_compile_sdf_compatibility_resize {soc_design output_dir} {
    set stream [open [file join $output_dir sdf_compatibility_resize.rpt] w]
    puts $stream "recipe=soc_nor_negative_iopath_v1"
    puts $stream "cell=U103023"
    puts $stream "required_source_library_cell=NangateOpenCellLibrary/NOR2_X2"
    puts $stream "required_target_library_cell=NangateOpenCellLibrary/NOR2_X1"
    set code [catch {
        if {[sizeof_collection $soc_design] != 1 ||
            [compare_collections [current_design] $soc_design] != 0 ||
            [get_object_name $soc_design] ne "soc_top"} {
            error "SDF compatibility resize requires the exact mapped soc_top context"
        }
        set cell [get_cells -quiet U103023]
        if {[sizeof_collection $cell] != 1 || [get_object_name $cell] ne "U103023" ||
            [get_attribute $cell is_hierarchical] || [get_attribute $cell ref_name] ne "NOR2_X2"} {
            error "SDF compatibility target changed; diagnose the new mapping instead of skipping or resizing another gate"
        }
        set old_library [get_lib_cells -quiet NangateOpenCellLibrary/NOR2_X2]
        set new_library [get_lib_cells -quiet NangateOpenCellLibrary/NOR2_X1]
        if {[sizeof_collection $old_library] != 1 || [sizeof_collection $new_library] != 1 ||
            [compare_collections [get_lib_cells -of_objects $cell] $old_library] != 0} {
            error "SDF compatibility source/target library identity differs"
        }
        foreach ref {NOR2_X2 NOR2_X1} {
            if {[sizeof_collection [get_lib_pins -quiet NangateOpenCellLibrary/$ref/*]] != 3} {
                error "Unexpected library pin interface on $ref"
            }
        }
        foreach {pin expected_direction} {A1 in A2 in ZN out} {
            foreach ref {NOR2_X2 NOR2_X1} {
                set library_pin [get_lib_pins -quiet NangateOpenCellLibrary/$ref/$pin]
                if {[sizeof_collection $library_pin] != 1} {error "Missing library pin $ref/$pin"}
                set direction [get_attribute $library_pin direction]
                if {$direction eq "input"} {set direction in}
                if {$direction eq "output"} {set direction out}
                puts $stream "library_pin=$ref/$pin direction=$direction"
                if {$direction ne $expected_direction} {error "Unexpected direction on $ref/$pin"}
            }
        }
        set old_function [soc_compile_unwrap_list [get_attribute \
            [get_lib_pins NangateOpenCellLibrary/NOR2_X2/ZN] function]]
        set new_function [soc_compile_unwrap_list [get_attribute \
            [get_lib_pins NangateOpenCellLibrary/NOR2_X1/ZN] function]]
        puts $stream "source_output_function=$old_function"
        puts $stream "target_output_function=$new_function"
        if {$old_function eq "" || $old_function ne $new_function} {
            error "SDF compatibility source/target output functions differ"
        }
        set expected_pins [list [list U103023/A1 in n124700] \
            [list U103023/A2 in n110435] [list U103023/ZN out n108666]]
        set before [soc_compile_sdf_compatibility_pins $cell]
        puts $stream "pins_before=$before"
        if {$before ne [lsort $expected_pins]} {
            error "SDF compatibility target connectivity changed; diagnose the new mapping before updating the recipe"
        }
        set cells_before [get_cells -quiet *]
        puts $stream "reference_before=[get_attribute $cell ref_name]"
        flush $stream
        redirect -tee -file [file join $output_dir sdf_compatibility_resize.log] {
            size_cell $cell NangateOpenCellLibrary/NOR2_X1
        }
        set resized [get_cells -quiet U103023]
        if {[sizeof_collection $resized] != 1 || [compare_collections $cell $resized] != 0 ||
            [get_attribute $resized ref_name] ne "NOR2_X1" ||
            [compare_collections [get_lib_cells -of_objects $resized] $new_library] != 0 ||
            [compare_collections [get_cells -quiet *] $cells_before] != 0} {
            error "SDF compatibility resize did not preserve cell identity and the top-level cell set"
        }
        set after [soc_compile_sdf_compatibility_pins $resized]
        puts $stream "reference_after=[get_attribute $resized ref_name]"
        puts $stream "pins_after=$after"
        if {$after ne $before} {error "SDF compatibility resize changed pin directions or net connections"}
        if {[compare_collections [current_design] $soc_design] != 0} {
            error "SDF compatibility resize changed the current design context"
        }
        puts $stream "cell_identity_preserved=true"
        puts $stream "top_level_cell_set_preserved=true"
        puts $stream "pin_connections_preserved=true"
        puts $stream "library_output_function_preserved=true"
    } message]
    if {$code} {
        puts $stream "status=FAIL"
        puts $stream "error=$message"
        close $stream
        error "SDF compatibility resize failed: $message"
    }
    puts $stream "status=PASS"
    close $stream
    puts "SOC_SDF_COMPATIBILITY_RESIZED cell=U103023 from=NOR2_X2 to=NOR2_X1"
}

proc soc_compile_main {} {
    if {![info exists ::env(SOC_COMPILE_CASE)]} {error "SOC_COMPILE_CASE is required"}
    set case_dir [file normalize $::env(SOC_COMPILE_CASE)]
    set input_dir [file join $case_dir inputs]
    set output_dir [file join $case_dir dc]
    file mkdir $output_dir
    source [file join $case_dir expected.tcl]
    foreach name {expected_top expected_top_inputs expected_data_inputs expected_output_bits
        expected_unmapped_count expected_pe_count expected_sram_count expected_wrapper_top
        expected_array_reference expected_wrapper_fingerprint expected_array_fingerprint} {
        if {![info exists $name]} {error "Missing frozen expectation: $name"}
    }
    foreach name {expected_wrapper_fingerprint expected_array_fingerprint} {
        if {![regexp {^[0-9a-f]{64}$} [set $name]]} {
            error "Invalid expected structural fingerprint: $name"
        }
    }
    set soc_ddc [file join $input_dir soc_constrained.ddc]
    set standard_db [file join $input_dir standard.db]
    set sram_db [file join $input_dir sram.db]
    set input_files [list $soc_ddc $standard_db $sram_db \
        [file join $input_dir soc_constraints.sdc] \
        [file join $input_dir pe_axi_fingerprint.tcl] \
        [file join $case_dir expected.tcl] [file normalize [info script]]]
    set input_hashes [eval exec sha256sum -- $input_files]
    source [file join $input_dir pe_axi_fingerprint.tcl]
    soc_compile_sdf_compatibility_inputs $input_dir $output_dir

    soc_compile_progress load_constrained_soc
    set_host_options -max_cores 1
    redirect -file [file join $output_dir host_options.rpt] {report_host_options}
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
    set_app_var auto_link_disable true
    redirect -file [file join $output_dir load.log] {
        set loaded_designs [read_ddc $soc_ddc]
        set_app_var auto_link_disable true
    }
    if {[lsearch -exact $loaded_designs $expected_top] < 0} {
        error "The frozen DDC does not contain the expected SoC top"
    }
    # DDC files can restore old local link attributes. Remove just those
    # attributes before a hierarchy query, preserving clocks and protection.
    set_app_var target_library [list $standard_db]
    set_app_var link_library [list * $standard_db $sram_db gtech.db]
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
    set soc_design [get_designs -quiet -exact "${soc_ddc}:$expected_top"]
    soc_compile_progress clear_obsolete_local_link_files
    foreach object $imported_objects {
        if {[soc_compile_unwrap_list [get_attribute -quiet $object local_link_library]] ne ""} {
            remove_attribute $object local_link_library
            if {[soc_compile_unwrap_list [get_attribute -quiet $object local_link_library]] ne ""} {
                error "Cannot clear an imported design's obsolete local link file"
            }
        }
    }
    soc_compile_select $soc_design
    set_app_var auto_link_disable false
    redirect -file [file join $output_dir link.log] {set linked [link]}
    if {!$linked} {error "Cannot link the frozen constrained SoC"}
    if {[compare_collections [add_to_collection [get_designs -hierarchical *] $soc_design] \
            $imported_designs] != 0} {
        error "The SoC hierarchy resolved outside the frozen imported DDC"
    }
    set untrusted_preexisting_designs [remove_from_collection [get_designs *] $imported_designs]
    if {[get_object_name [current_design]] ne $expected_top ||
        [sizeof_collection [all_inputs]] != $expected_top_inputs ||
        [sizeof_collection [all_outputs]] != $expected_output_bits ||
        [sizeof_collection [remove_from_collection [all_inputs] [get_ports {clk rst_n}]]] != $expected_data_inputs} {
        error "Unexpected whole-SoC bit interface"
    }
    set core_clock [soc_compile_clock]
    redirect -variable unit_report {report_units}
    if {![regexp {Time_unit\s+:\s+1\.0e-09} $unit_report] ||
        ![regexp {Capacitive_load_unit\s+:\s+1\.0e-15} $unit_report]} {
        error "Whole-SoC compilation expects verified ns/fF units"
    }
    set pe_cell [get_cells -quiet u_pe]
    set wrapper_design [get_designs -hierarchical -quiet -exact $expected_wrapper_top]
    if {[sizeof_collection $pe_cell] != 1 ||
        [get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [get_attribute -quiet $pe_cell dont_touch] ne "true" ||
        [sizeof_collection $wrapper_design] != 1 ||
        ![get_attribute $wrapper_design is_mapped]} {
        error "Expected the protected, completely mapped real u_pe accelerator"
    }
    set unmapped_before [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {[get_attribute $soc_design is_mapped] || $unmapped_before != $expected_unmapped_count} {
        error "Expected the known still-unmapped CPU/router before compilation"
    }
    set ports_before [soc_compile_ports [file join $output_dir top_ports_before.tsv]]
    set pins_before [soc_compile_pe_pins [file join $output_dir pe_pins_before.tsv]]
    redirect -file [file join $output_dir constraints_before.log] {
        if {![write_sdc [file join $output_dir soc_constraints_before.sdc]]} {
            error "Cannot save the actual loaded constraints"
        }
        report_port -verbose -nosplit -significant_digits 6
    }

    soc_compile_select $wrapper_design
    if {[sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]] != 0} {
        error "The reused accelerator contains unmapped logic"
    }
    set wrapper_leaves_before [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false"]]
    set array_cell [get_cells -quiet u_pe_array]
    if {[sizeof_collection $array_cell] != 1 ||
        [get_attribute $array_cell ref_name] ne $expected_array_reference} {
        error "The reused accelerator has a different PE array"
    }
    set array_design [get_designs -hierarchical -quiet -exact $expected_array_reference]
    if {[sizeof_collection $array_design] != 1 || ![get_attribute $array_design is_mapped]} {
        error "Expected the exact mapped array design"
    }
    set protected_designs [add_to_collection [get_designs -hierarchical *] $wrapper_design]
    soc_compile_select $soc_design
    soc_compile_progress fingerprint_accelerator_before
    set wrapper_before [pe_axi_array_fingerprint $wrapper_design [file join $output_dir pe_before.txt]]
    set array_before [pe_axi_array_fingerprint $array_design [file join $output_dir array_before.txt]]
    if {$wrapper_before ne $expected_wrapper_fingerprint || $array_before ne $expected_array_fingerprint} {
        error "The frozen accelerator or array differs from the previously verified structure"
    }

    # Protect both the instance boundary and shared mapped design. No new
    # budget is sourced; the constrained checkpoint is the only timing input.
    set_dont_touch $pe_cell true
    set_dont_touch $wrapper_design true
    set_ungroup $pe_cell false
    set_boundary_optimization $pe_cell false
    if {[get_attribute -quiet $pe_cell dont_touch] ne "true" ||
        [get_attribute -quiet $wrapper_design dont_touch] ne "true"} {
        error "Cannot protect the reused mapped accelerator"
    }
    set_fix_hold $core_clock
    soc_compile_progress compile_cpu_router
    redirect -tee -file [file join $output_dir compile.log] {set compiled [compile]}
    if {!$compiled} {error "Whole-SoC CPU/router mapping failed"}
    # Save immediately: later interface/fingerprint/report checks may reject
    # the result. These files are explicitly UNVERIFIED and are never the
    # accepted soc_mapped outputs or evidence of synthesis/timing acceptance.
    soc_compile_progress save_unverified_post_compile_checkpoint
    redirect -file [file join $output_dir post_compile_unverified_save.log] {
        if {![write -format ddc -hierarchy \
                -output [file join $output_dir post_compile_unverified.ddc]]} {
            error "Cannot save the unverified post-compile DDC checkpoint"
        }
        if {![write_sdc [file join $output_dir post_compile_unverified.sdc]]} {
            error "Cannot save the unverified post-compile SDC checkpoint"
        }
        if {![write -format verilog -hierarchy \
                -output [file join $output_dir post_compile_unverified.v]]} {
            error "Cannot save the unverified post-compile Verilog checkpoint"
        }
    }
    soc_compile_progress resize_diagnosed_negative_sdf_path
    soc_compile_sdf_compatibility_resize $soc_design $output_dir
    if {[compare_collections [current_design] $soc_design] != 0 ||
        ![get_attribute $soc_design is_mapped]} {
        error "The mapped whole-SoC top is not the intended design"
    }
    set unmapped_after [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {$unmapped_after != 0} {error "Unmapped leaf logic remains after compilation"}
    set pe_cell [get_cells -quiet u_pe]
    if {[sizeof_collection $pe_cell] != 1 ||
        [get_attribute $pe_cell ref_name] ne $expected_wrapper_top ||
        [get_attribute -quiet $pe_cell dont_touch] ne "true" ||
        [get_attribute -quiet $wrapper_design dont_touch] ne "true" ||
        [compare_collections [get_designs -hierarchical -quiet -exact $expected_wrapper_top] $wrapper_design] != 0} {
        error "The protected accelerator instance or definition was replaced"
    }
    set reachable_after [add_to_collection [get_designs -hierarchical *] $soc_design]
    if {[sizeof_collection [remove_from_collection $protected_designs $reachable_after]] != 0} {
        error "An original protected accelerator definition left the SoC hierarchy"
    }
    set unrelated_reachable [remove_from_collection \
        [remove_from_collection $reachable_after $imported_designs] $soc_design]
    # compile may create fresh CPU/router definitions. They are permitted only
    # if they were not among pre-existing duplicate candidates from read_ddc.
    # The original accelerator definitions must remain exact objects above.
    if {[compare_collections \
        [remove_from_collection $untrusted_preexisting_designs $reachable_after] \
        $untrusted_preexisting_designs] != 0} {
        error "Synthesis made a pre-existing unrelated candidate reachable from soc_top"
    }
    soc_compile_write_records [file join $output_dir reachable_designs_after.tsv] \
        [list [list reachable_count [sizeof_collection $reachable_after]] \
        [list newly_reachable_names [lsort [get_object_name $unrelated_reachable]]]]
    set pe_count [sizeof_collection [get_cells -hierarchical -filter "ref_name =~ *pe_1sram_4mul"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != $expected_pe_count || $sram_count != $expected_sram_count ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected PE count or real SRAM library binding was not preserved"
    }
    set ports_after [soc_compile_ports [file join $output_dir top_ports_after.tsv]]
    set pins_after [soc_compile_pe_pins [file join $output_dir pe_pins_after.tsv]]
    if {$ports_before ne $ports_after || $pins_before ne $pins_after} {
        error "Whole-SoC or accelerator bit interface changed during compilation"
    }
    soc_compile_clock
    soc_compile_select $wrapper_design
    set array_cell [get_cells -quiet u_pe_array]
    if {[sizeof_collection $array_cell] != 1 ||
        [get_attribute $array_cell ref_name] ne $expected_array_reference ||
        [compare_collections [get_designs -hierarchical -quiet -exact $expected_array_reference] $array_design] != 0 ||
        [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]] != $wrapper_leaves_before} {
        error "The protected array definition or accelerator leaf count changed"
    }
    soc_compile_select $soc_design
    soc_compile_progress fingerprint_accelerator_after
    set wrapper_after [pe_axi_array_fingerprint $wrapper_design [file join $output_dir pe_after.txt]]
    set array_after [pe_axi_array_fingerprint $array_design [file join $output_dir array_after.txt]]
    if {$wrapper_before ne $wrapper_after || $array_before ne $array_after} {
        error "The protected accelerator cells, ports or connections changed during compilation"
    }

    soc_compile_progress reports
    redirect -file [file join $output_dir check_design.rpt] {set design_ok [check_design]}
    if {!$design_ok} {error "Mapped SoC check_design failed"}
    redirect -file [file join $output_dir area.rpt] {report_area}
    redirect -file [file join $output_dir setup.rpt] {
        report_timing -delay_type max -max_paths 10 -significant_digits 6
    }
    redirect -file [file join $output_dir hold.rpt] {
        report_timing -delay_type min -max_paths 10 -significant_digits 6
    }
    redirect -file [file join $output_dir violations.rpt] {
        report_constraint -all_violators -significant_digits 6
    }
    redirect -file [file join $output_dir constraints_check.rpt] {
        check_timing -include {no_input_delay partial_input_delay unconstrained_endpoints}
    }
    redirect -file [file join $output_dir ports.rpt] {report_port -verbose -nosplit -significant_digits 6}
    redirect -file [file join $output_dir clocks.rpt] {
        report_clock -attributes -nosplit
        report_clock -skew -nosplit
    }
    redirect -file [file join $output_dir units.rpt] {report_units}
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[sizeof_collection $setup_paths] == 0 || [sizeof_collection $hold_paths] == 0} {
        error "Mapped SoC is missing setup or hold paths"
    }
    set setup_slack 1e30
    set hold_slack 1e30
    foreach_in_collection path $setup_paths {
        set setup_slack [expr {min($setup_slack, [get_attribute $path slack])}]
    }
    foreach_in_collection path $hold_paths {
        set hold_slack [expr {min($hold_slack, [get_attribute $path slack])}]
    }
    set nonnegative_slack [expr {$setup_slack >= 0.0 && $hold_slack >= 0.0}]

    soc_compile_progress save_mapped_soc
    redirect -file [file join $output_dir save.log] {
        if {![write_sdc [file join $output_dir soc_constraints.sdc]] ||
            ![write -format ddc -hierarchy -output [file join $output_dir soc_mapped.ddc]] ||
            ![write -format verilog -hierarchy -output [file join $output_dir soc_mapped.v]]} {
            error "Cannot save the mapped SoC and actual constraints"
        }
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An input changed during whole-SoC synthesis"
    }
    set stream [open [file join $output_dir synthesis_summary.rpt] w]
    foreach {key value} [list \
        top $expected_top input_bits $expected_top_inputs data_input_bits $expected_data_inputs \
        output_bits $expected_output_bits clock_name core_clk period_ns 10 \
        cpu_router_unmapped_leaf_cells_before $unmapped_before unmapped_leaf_cells $unmapped_after \
        pe_instances $pe_count sram_instances $sram_count array_reference $expected_array_reference \
        mapped_pe_reference $expected_wrapper_top pe_instance_protected true pe_design_protected true \
        mapped_pe_fingerprint_before $wrapper_before mapped_pe_fingerprint_after $wrapper_after \
        array_fingerprint_before $array_before array_fingerprint_after $array_after \
        accelerator_leaf_objects $wrapper_leaves_before \
        imported_designs [llength $loaded_designs] obsolete_local_links_cleared true \
        scoped_in_memory_linking true bit_interfaces_preserved true \
        accelerator_structure_preserved true check_design_success true \
        setup_slack_ns $setup_slack hold_slack_ns $hold_slack \
        setup_and_hold_slack_nonnegative $nonnegative_slack \
        compile_performed true set_fix_hold true max_cpu_cores 1 soc_mapped true \
        sta_acceptance_established false simulation_performed false \
        physical_parasitics_used false formal_equivalence_checked false] {
        puts $stream "$key=$value"
    }
    close $stream
    puts "SOC_SETUP_SLACK_NS=$setup_slack"
    puts "SOC_HOLD_SLACK_NS=$hold_slack"
    puts "DC_SOC_SYNTHESIS_DONE mapped=true unmapped_leaf_cells=0 sram_instances=$sram_count"
    puts "SOC_SYNTHESIS_REVIEW: inspect setup.rpt, hold.rpt and violations.rpt; DONE is not timing acceptance."
    flush stdout
}

if {[catch {soc_compile_main} soc_compile_error]} {
    puts stderr "SOC_COMPILE_FAILED: $soc_compile_error"
    if {[info exists ::errorInfo]} {puts stderr $::errorInfo}
    exit 1
}
exit 0
