# Read the real AXI control RTL around the already mapped 16x16 array.
# Structure only: the new AXI logic is not mapped or timed by this step.
proc pe_axi_select_design {design_object} {
    if {[sizeof_collection $design_object] != 1} {
        error "Expected exactly one design object when switching context"
    }
    current_design $design_object
    if {[compare_collections [current_design] $design_object] != 0} {
        error "Cannot select the requested design; stopping before changing constraints"
    }
}

proc read_pe_axi_system {project_dir {output_dir ""}} {
    if {$output_dir eq ""} {
        set output_dir [file join $project_dir build dc_pe_axi]
    }
    set library_dir [file join $project_dir build dc_libraries]
    set array_ddc [file join $project_dir build dc_pe_array_export pe_array_mapped.ddc]
    set wrapper_rtl [file join $project_dir rtl pe_axi_wrapper.v]
    set cpu_config [file join $project_dir snapshots soc common_defines.vh]
    file mkdir [file join $output_dir work]
    set input_files [list $array_ddc $wrapper_rtl $cpu_config \
        [file join $library_dir standard.db] [file join $library_dir sram.db]]
    set input_hashes [eval exec sha256sum -- $input_files]
    set stream [open $cpu_config r]
    set config_text [read $stream]
    close $stream
    if {![regexp -line {^\s*`define\s+RV_LSU_BUS_TAG\s+(\d+)\s*$} $config_text -> id_width] ||
        $id_width != 3} {
        error "Expected the frozen CPU's 3-bit LSU AXI ID configuration"
    }
    set_app_var target_library [list [file join $library_dir standard.db]]
    # Use this DDC explicitly. Searching every in-memory design with '*' can
    # select an older PE/array copy in a long-lived interactive session.
    set_app_var link_library [list $array_ddc [file join $library_dir standard.db] \
        [file join $library_dir sram.db] gtech.db]
    puts "PE_AXI_PHASE=load_array"
    flush stdout
    if {[sizeof_collection [get_libs -quiet NangateOpenCellLibrary]] == 0} {
        read_db [file join $library_dir standard.db]
    }
    if {[sizeof_collection [get_libs -quiet SRAM_6T_CORE_16x8_MC_TB]] == 0} {
        read_db [file join $library_dir sram.db]
    }
    if {[sizeof_collection [get_lib_cells -quiet NangateOpenCellLibrary/*]] != 135 ||
        [sizeof_collection [get_lib_cells -quiet SRAM_6T_CORE_16x8_MC_TB/*]] != 1} {
        error "Expected the standard and SRAM libraries"
    }
    set loaded_designs {}
    redirect -file [file join $output_dir array_load.log] {
        set loaded_designs [read_ddc $array_ddc]
    }
    if {[lsearch -exact $loaded_designs pe_array] < 0} {
        error "Cannot load the mapped array; inspect $output_dir/array_load.log"
    }
    # Reading a DDC can restore its saved link environment. Reapply the
    # explicit file list after import, before any automatic hierarchy lookup.
    set_app_var link_library [list $array_ddc [file join $library_dir standard.db] \
        [file join $library_dir sram.db] gtech.db]
    # read_ddc selects its top design. Set its local search order before
    # queries can trigger automatic linking against older in-memory copies.
    if {[get_object_name [current_design]] ne "pe_array"} {
        error "The imported DDC did not select its pe_array top"
    }
    set_local_link_library [list $array_ddc]
    set imported_objects {}
    set imported_collection ""
    foreach imported_design $loaded_designs {
        set qualified_design "${array_ddc}:$imported_design"
        set design_object [get_designs -quiet -exact $qualified_design]
        if {[sizeof_collection $design_object] != 1} {
            error "Cannot uniquely resolve imported design $qualified_design"
        }
        lappend imported_objects $design_object
        set imported_collection [add_to_collection $imported_collection $design_object]
    }
    set array_design [get_designs -exact "${array_ddc}:pe_array"]
    # Array/PE boundary budgets do not apply at the new AXI boundary.
    # Reset only designs imported from the DDC; preserve their mapped cells.
    puts "PE_AXI_PHASE=clear_old_constraints designs=[llength $imported_objects]"
    flush stdout
    close [open [file join $output_dir clear_array_constraints.log] w]
    set cleared_count 0
    foreach design_object $imported_objects {
        redirect -append -file [file join $output_dir clear_array_constraints.log] {
            pe_axi_select_design $design_object
            if {![reset_design]} {
                error "Cannot clear the imported design's old constraints"
            }
            set_local_link_library [list $array_ddc]
        }
        incr cleared_count
        if {$cleared_count % 25 == 0 || $cleared_count == [llength $imported_objects]} {
            puts "PE_AXI_CONSTRAINTS_CLEARED=$cleared_count/[llength $imported_objects]"
            flush stdout
        }
    }
    pe_axi_select_design $array_design
    puts "PE_AXI_PHASE=link_reused_array"
    flush stdout
    redirect -file [file join $output_dir array_link.log] {
        set array_linked [link]
    }
    if {!$array_linked ||
        [compare_collections [add_to_collection [get_designs -hierarchical *] $array_design] $imported_collection] != 0} {
        error "The array hierarchy did not link exclusively to the imported DDC"
    }
    if {![get_attribute [current_design] is_mapped]} {
        error "Expected the imported array to remain mapped"
    }
    set imported_leaf_count [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false"]]
    define_design_lib PE_AXI_WORK -path [file join $output_dir work]
    puts "PE_AXI_PHASE=elaborate_axi_control"
    flush stdout
    # Despite the .v suffix, this file uses SystemVerilog loop declarations.
    redirect -file [file join $output_dir wrapper_analyze.log] {
        set analyzed [analyze -library PE_AXI_WORK -format sverilog $wrapper_rtl]
    }
    if {!$analyzed} {
        error "Cannot analyze the AXI wrapper; inspect $output_dir/wrapper_analyze.log"
    }
    redirect -file [file join $output_dir wrapper_elaborate.log] {
        set elaborated [elaborate sram_multiplier_system -library PE_AXI_WORK \
            -parameters "ID_WIDTH=$id_width"]
    }
    if {!$elaborated} {
        error "Cannot elaborate the AXI wrapper; inspect $output_dir/wrapper_elaborate.log"
    }
    set system_top [get_object_name [current_design]]
    set system_design [current_design]
    reset_design
    pe_axi_select_design $array_design
    # DC 2016 can retain an elaborated template's old binding even after a
    # qualified change_link. Give this imported hierarchy unique in-memory
    # names; the source RTL and exported DDC remain unchanged.
    set reuse_prefix "pe_axi_reuse_[pid]_[clock clicks]_"
    set array_alias "${reuse_prefix}pe_array"
    set pe_reference "${reuse_prefix}pe_1sram_4mul"
    if {![rename_design $imported_collection -prefix $reuse_prefix -dont_link_with_original_name]} {
        error "Cannot give the imported array hierarchy unambiguous names"
    }
    set imported_objects {}
    set imported_collection ""
    foreach imported_design $loaded_designs {
        set imported_design "${reuse_prefix}$imported_design"
        set design_object [get_designs -quiet -exact "${array_ddc}:$imported_design"]
        if {[sizeof_collection $design_object] != 1} {
            error "Cannot resolve the renamed array hierarchy"
        }
        lappend imported_objects $design_object
        set imported_collection [add_to_collection $imported_collection $design_object]
    }
    set array_design [get_designs -exact "${array_ddc}:$array_alias"]
    pe_axi_select_design $system_design
    set_local_link_library [list $array_ddc]
    # Elaboration can bind an existing HDL template before link_library is
    # consulted. Explicitly replace that instance with this DDC's array.
    if {[sizeof_collection [get_cells u_pe_array]] != 1 ||
        ![change_link [get_cells u_pe_array] "${array_ddc}:$array_alias"]} {
        error "Cannot bind u_pe_array to the requested mapped array"
    }
    puts "PE_AXI_PHASE=link"
    flush stdout
    redirect -file [file join $output_dir link.log] {
        set linked [link]
    }
    if {!$linked} {
        error "AXI wrapper link failed; inspect $output_dir/link.log"
    }
    foreach {pattern expected} {s_axi_awid* 3 s_axi_arid* 3 s_axi_bid* 3 s_axi_rid* 3 s_axi_wdata* 64 s_axi_rdata* 64} {
        if {[sizeof_collection [get_ports $pattern]] != $expected} {
            error "Unexpected AXI port width: $pattern"
        }
    }
    if {[sizeof_collection [all_inputs]] != 176 ||
        [sizeof_collection [all_outputs]] != 80 ||
        [sizeof_collection [get_cells u_pe_array]] != 1} {
        error "Expected the frozen 3-bit-ID AXI interface and one array"
    }
    set array_reference [get_attribute [get_cells u_pe_array] ref_name]
    if {$array_reference ne $array_alias} {
        error "AXI wrapper did not reuse the imported pe_array: $array_reference"
    }
    if {[compare_collections [get_designs -hierarchical $array_alias] $array_design] != 0} {
        error "AXI wrapper linked to a different in-memory array copy"
    }
    set expected_system_designs [add_to_collection $imported_collection $system_design]
    if {[compare_collections [add_to_collection [get_designs -hierarchical *] $system_design] $expected_system_designs] != 0} {
        error "AXI system contains a hierarchy from an older DDC copy"
    }
    puts "PE_AXI_PHASE=check_structure"
    flush stdout
    set pe_count [sizeof_collection [get_cells -hierarchical -filter "ref_name == $pe_reference"]]
    set sram_cells [get_cells -hierarchical -filter "ref_name == SRAM_6T_CORE_16x8_MC_TB"]
    set sram_count [sizeof_collection $sram_cells]
    if {$pe_count != 256 || $sram_count != 256 ||
        [sizeof_collection [get_lib_cells -of_objects $sram_cells]] != 1} {
        error "Expected 256 PEs and 256 SRAMs bound to the real library"
    }
    set system_unmapped [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {[get_attribute [current_design] is_mapped] || $system_unmapped == 0} {
        error "Expected the new AXI control logic to be elaborated, not mapped"
    }
    pe_axi_select_design $array_design
    set array_unmapped [sizeof_collection [get_cells -hierarchical \
        -filter "is_hierarchical == false && is_unmapped == true"]]
    if {![get_attribute [current_design] is_mapped] || $array_unmapped != 0 ||
        [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical == false"]] != $imported_leaf_count} {
        error "The reused array's mapped structure was not preserved"
    }
    redirect -file [file join $output_dir array_clocks.log] {
        foreach design_object $imported_objects {
            pe_axi_select_design $design_object
            if {[sizeof_collection [get_clocks -quiet *]] != 0} {
                error "Stale clocks remain in an imported design"
            }
        }
    }
    pe_axi_select_design $system_design
    if {[sizeof_collection [get_clocks -quiet *]] != 0} {
        error "AXI boundary constraints have not yet been set"
    }
    redirect -file [file join $output_dir check_design.rpt] {
        set design_ok [check_design]
    }
    if {!$design_ok} {
        error "Structural checks failed; inspect $output_dir/check_design.rpt"
    }
    puts "PE_AXI_PHASE=save"
    flush stdout
    if {![write -format ddc -hierarchy -output [file join $output_dir pe_axi_structure.ddc]]} {
        error "Cannot save the AXI-controlled array structure"
    }
    if {[eval exec sha256sum -- $input_files] ne $input_hashes} {
        error "An input file changed during the import"
    }
    set stream [open [file join $output_dir inputs.sha256] w]
    puts $stream $input_hashes
    close $stream
    set stream [open [file join $output_dir structure.rpt] w]
    puts $stream "top=$system_top id_width=$id_width rows=16 columns=16"
    puts $stream "array_reference=$array_reference pe_instances=$pe_count sram_instances=$sram_count"
    puts $stream "array_mapped=true array_unmapped_leaf_cells=$array_unmapped array_leaf_objects=$imported_leaf_count"
    puts $stream "system_mapped=false system_unmapped_leaf_cells=$system_unmapped clock_count=0"
    puts $stream "scope=AXI control RTL linked to reused mapped array; no compile, STA, AXI simulation or CPU"
    close $stream
    puts "DC_READ_PE_AXI_PASS id_width=$id_width pe_instances=$pe_count sram_instances=$sram_count"
}
read_pe_axi_system [file dirname [file dirname [file normalize [info script]]]]
