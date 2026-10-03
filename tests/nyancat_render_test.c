/* Native test with generated assets: delta updates must match the full image.
 * cc -fsanitize=address,undefined -I build/nyancat tests/nyancat_render_test.c ...
 * No hardware, BIOS trap or audio behavior is inferred from this test. */
#include <stdint.h>
#include <stdio.h>
#include <assert.h>
static int32_t bios_call(unsigned fn,uintptr_t a,uintptr_t b,uintptr_t c,uintptr_t d) {
    (void)fn;(void)a;(void)b;(void)c;(void)d;return 0;
}
#include "../firmware/examples/nyancat_demo.c"
const int16_t nyan_pcm[2]={0},nyan_pcm_end[2]={0};
static uint16_t screens[2][WIDTH*HEIGHT];
int main(void) {
    for(unsigned i=0;i<2;++i)rect(screens[i],0,0,WIDTH,HEIGHT,sky);
    for(unsigned tick=0;tick<120;++tick) {
        slot=tick&1;frame=tick%NYAN_FRAMES;render(screens[slot],tick);
        for(unsigned y=0;y<NYAN_ROWS*SCALE;++y)for(unsigned x=0;x<NYAN_COLS*SCALE;++x) {
            unsigned color=nyan_frames[frame][(y/SCALE)*NYAN_COLS+x/SCALE];
            uint16_t expected=color?nyan_palette[color]:sky;
            if(!color && CAT_X+x<160) {
                int ry=(int)(CAT_Y+y)-(int)(76+(((CAT_X+x)/16+tick)&1)*4);
                if(ry>=0 && ry<120)expected=rainbow[ry/20];
            }
            uint16_t got=screens[slot][(CAT_Y+y)*WIDTH+CAT_X+x];
            if(got!=expected)fprintf(stderr,"tick=%u x=%u y=%u got=%04x expected=%04x\n",tick,x,y,got,expected);
            assert(got==expected);
        }
    }
    puts("PASS 120 alternating delta frames equal full GIF/rainbow composition");
}
