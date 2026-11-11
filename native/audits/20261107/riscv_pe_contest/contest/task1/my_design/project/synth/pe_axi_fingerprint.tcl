# Describe the mapped array's structure without exporting or changing it.
# Visit each shared design definition once; do not expand all 256 PE copies.
# No compilation or timing analysis is performed by this helper.
proc pe_axi_array_fingerprint {array_design path} {
    set saved_design [current_design]
    if {[sizeof_collection $saved_design] != 1 ||
        [sizeof_collection $array_design] != 1} {
        error "Fingerprint requires one current design and one array design object"
    }
    set stream ""
    set failure [catch {
        redirect -variable context_message {current_design $array_design}
        if {[compare_collections [current_design] $array_design] != 0} {
            error "Cannot select the array for structural fingerprinting"
        }
        set designs [add_to_collection [get_designs -hierarchical *] $array_design]
        set records {}
        set completed 0
        set total [sizeof_collection $designs]
        foreach_in_collection design $designs {
            redirect -variable context_message {current_design $design}
            if {[compare_collections [current_design] $design] != 0} {
                error "Cannot select an array subdesign for structural fingerprinting"
            }
            set design_name [get_object_name $design]
            lappend records [list DESIGN $design_name]
            foreach_in_collection cell [get_cells -quiet *] {
                set dont_touch_value [get_attribute -quiet $cell dont_touch]
                if {$dont_touch_value eq "false" || $dont_touch_value eq "0"} {
                    error "Explicit dont_touch=false would override array protection in $design_name"
                }
                set reference [get_attribute $cell ref_name]
                if {$reference eq ""} {
                    error "Missing cell reference in array design $design_name"
                }
                lappend records [list CELL $design_name [get_object_name $cell] $reference]
            }
            foreach_in_collection port [get_ports -quiet *] {
                set direction [get_attribute $port direction]
                if {$direction eq ""} {
                    error "Missing port direction in array design $design_name"
                }
                lappend records [list PORT $design_name [get_object_name $port] $direction]
            }
            foreach_in_collection net [get_nets -quiet *] {
                # Without -leaf, all_connected retains the local hierarchy
                # boundary. In particular, a shared PE is not flattened here.
                set endpoints [lsort [get_object_name [all_connected $net]]]
                lappend records [list NET $design_name [get_object_name $net] $endpoints]
            }
            incr completed
            if {$completed % 25 == 0 || $completed == $total} {
                puts "PE_AXI_ARRAY_FINGERPRINT=$completed/$total"
                flush stdout
            }
        }
        file mkdir [file dirname $path]
        set stream [open $path w]
        foreach record [lsort $records] {
            puts $stream $record
        }
        close $stream
        set stream ""
        set fingerprint [lindex [exec sha256sum -- $path] 0]
        if {![regexp {^[0-9a-f]{64}$} $fingerprint]} {
            error "Cannot obtain the array structural fingerprint"
        }
    } message]
    if {$stream ne ""} {
        catch {close $stream}
    }
    # Restore the caller's exact design object on both success and failure.
    set restore_failed [catch {
        redirect -variable context_message {current_design $saved_design}
        if {[compare_collections [current_design] $saved_design] != 0} {
            error "Restored design object differs from the caller's context"
        }
    } restore_message]
    if {$restore_failed} {
        error "Array fingerprint context restoration failed: $restore_message; original status=$failure detail=$message"
    }
    if {$failure} {
        error "Array structural fingerprint failed: $message"
    }
    return $fingerprint
}
