#
# Headless Gowin DDR3 Memory Interface IP generator (deterministic, no GUI).
#
#   gw_sh.exe gen_ddr3_ip.tcl <out_dir> <memory_clk_mhz> <module_name>
#
# Produces <out_dir>/ip/<file_name>/<file_name>.v  (encrypted GowinSynthesis netlist)
#          <out_dir>/ip/<file_name>/<file_name>.vo (pre-synthesised output netlist)
#          <out_dir>/ip/<file_name>/<file_name>_tmp.v (instantiation template)
#          <out_dir>/ip/<file_name>/<file_name>.ipc (resolved parameter record)
#          <out_dir>/ip/<file_name>/temp/DDR3/gwmc_param.v (controller parameters)
#
# Parameter set == the vendor reference example
#   TangPrimer-138K/example/ddr_test/Hynix_400MHz/src/ddr3_memory_interface/DDR3_400.ipc
# (32-bit DQ, single rank, 15-bit row, 10-bit col, CL=6 CWL=5, DDR3-800 timings).
# Only Memory_Clock is varied -> tCK scales with 1/f, t* stay absolute (ps).
#
set argc_ [llength $argv]
if {$argc_ < 2} { puts "USAGE: gw_sh gen_ddr3_ip.tcl <out_dir> <memory_clk_mhz> ?module_name?"; exit 1 }
set OUT    [lindex $argv 0]
set CLK    [lindex $argv 1]
set MODULE [expr {$argc_ > 2 ? [lindex $argv 2] : "D3_400"}]
set FILE   "${MODULE}.v"
set root   [file normalize $OUT]
file mkdir $root

if {[catch {create_project -name ipgen -dir $root/proj -pn GW5AST-LV138FPG676AES -device_version B -force} e]} {
  puts "CREATE_ERR: <$e>"; exit 1
}
if {[catch {create_ipc -name ddr3 -module_name $MODULE -dir $root/ip -language Verilog -file_name $FILE} e]} {
  puts "IPC_ERR: <$e>"; exit 1
}
set ip [get_ips $MODULE]

proc sp {name value} {
  global ip
  if {[catch {set_property -name CONFIG.$name -value $value $ip} e]} {
    puts "SET_ERR $name=$value <$e>"; exit 1
  }
}

# --- structure ---
sp User_Interface   Controller
sp Memory_Type      Components
sp CLK_Ratio        1:4
sp Dq_Width         32
sp Dram_Width       16
sp Ecc              false
sp Rank_Address     1
sp Row_Address      15
sp Column_Address   10
sp Burst_Mode       8
sp Burst_Type       SEQ
sp CAS_Latency      6
sp CW_Latency       5
sp Additive_Latency 0
sp Wrire_Recovery   6
sp Rtt_Nom          60
sp Rtt_Wr           OFF
sp Addr_Cmd_Mode    1T
sp OUTPUT_DRV       LOW
sp User_Refresh     false
# --- absolute DRAM timings (ps), taken from the vendor part selection ---
sp tRTP_Period      7500
sp tRP_Period       15000
sp tWTR_Period      7500
sp tRC_Period       55000
sp tRAS_Period      37500
sp tRCD_Period      15000
sp tFAW_Period      40000
sp tRRD_Period      10000
sp tCKE_Period      10000
sp tREFI_Period     7800000
sp tRFC_Period      260000
sp tDLLK            512
# --- debug / memory buffer defaults (all off) ---
sp Debug_Parameter_Enable false
sp Debug_Port_Enable  false
sp MC_BSRAM           true
sp MC_SSRAM           false
sp MC_REG             false
sp AXI4_Interface     false
# --- clock last: derived options (tCK) are recomputed from it ---
sp Memory_Clock      $CLK

if {[catch {generate_target $ip} e]} { puts "GEN_ERR: <$e>"; exit 1 }

set ipc "$root/ip/$FILE/$FILE.ipc"
set f [open $ipc r]; set txt [read $f]; close $f
puts "=====IPC_BEGIN====="
puts $txt
puts "=====IPC_END====="
puts "GEN_OK out=$root/ip/$FILE clk=$CLK module=$MODULE"
exit 0
