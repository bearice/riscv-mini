"""Compile and exercise the actual fbdev pan callback and its CSR writes."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT/"firmware/linux/riscv_mini_fb.c").read_text(encoding="utf-8")
callback = "static int mini_pan_display(" + source.split("static int mini_pan_display(", 1)[1].split("static const struct fb_ops", 1)[0]
fixture = r"""
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#define MINI_WIDTH 480
#define MINI_HEIGHT 272
#define FB_VMODE_YWRAP 256
#define EINVAL 22
typedef uint32_t u32;
struct mini_fb { u32 dma; u32 regs[2]; bool wrap; };
enum mini_reg { BASE0, BASE1 };
struct fb_info { void *par; struct { u32 line_length; } fix; };
struct fb_var_screeninfo { u32 xoffset, yoffset, yres_virtual, vmode; };
static u32 *mini_reg(struct mini_fb *fb, enum mini_reg reg) { return &fb->regs[reg]; }
static unsigned int barriers, writes;
#define wmb() (++barriers)
static void writel_relaxed(u32 value, u32 *reg) { assert(barriers); writes++; *reg=value; }
""" + callback + r"""
int main(void) {
    struct mini_fb fb={.dma=0x01000000,.wrap=true}; struct fb_info info={&fb,{960}};
    struct fb_var_screeninfo var={.yres_virtual=272,.vmode=FB_VMODE_YWRAP};
    for(unsigned int offset=0;offset<272;offset++) {
        var.yoffset=offset;
        assert(mini_pan_display(&var,&info)==0);
        assert(fb.regs[0]==fb.dma+offset*960 && fb.regs[1]==fb.regs[0]);
        assert((fb.regs[0]&15)==0);
        assert(fb.regs[0]<fb.dma+480*272*2);
    }
    unsigned int old_writes=writes;
    var.yoffset=272; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.yoffset=0; var.xoffset=1; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.xoffset=0; var.yres_virtual=271; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.yres_virtual=273; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.yres_virtual=272; var.yoffset=UINT32_MAX; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.yoffset=1; var.vmode=0; assert(mini_pan_display(&var,&info)==-EINVAL);
    assert(writes==old_writes);
    var.yoffset=0; assert(mini_pan_display(&var,&info)==0); assert(fb.regs[0]==fb.dma);
    fb.wrap=false; var.vmode=FB_VMODE_YWRAP; assert(mini_pan_display(&var,&info)==-EINVAL);
    var.vmode=0; assert(mini_pan_display(&var,&info)==0);
    puts("PASS actual fb pan: all 272 ring offsets aligned/in allocation, bounds, old hardware, rewind");
}
"""
out=ROOT/"build/reports/checks/linux-fb-pan"; out.mkdir(parents=True,exist_ok=True)
cfile=out/"pan.c"; cfile.write_text(fixture,encoding="utf-8")
cc=Path("C:/msys64/ucrt64/bin/gcc.exe")
env={**os.environ,"PATH":str(cc.parent)+os.pathsep+os.environ["PATH"],"TEMP":str(out),"TMP":str(out),"TMPDIR":str(out)}
subprocess.run([str(cc),"-std=c11","-Wall","-Wextra","-Werror",str(cfile),"-o",str(out/"pan.exe")],env=env,check=True)
subprocess.run([str(out/"pan.exe")],env=env,check=True)
