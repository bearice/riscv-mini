/* Live I2S microphone snapshots on the RGB LCD; serial tests stay in firmware. */
#include <hal/hal.h>
#include <generated/csr.h>
#include "rgb_canvas.h"
enum { BG=0x1084, GRID=0x2945, WHITE=0xffff, GREEN=0x07e0, CYAN=0x07ff, YELLOW=0xffe0 };
enum { X=16, Y=62, W=448, H=160, CENTER=142, N=512 };
static int32_t samples[N],minimum,maximum,mean;
static unsigned active,windows,errors,right,paused,dirty,automatic=1,gain_shift=8,draw_shift=8,peak;
static int old_y[2][W];static unsigned drawn[2];

static uint16_t background_pixel(unsigned x,unsigned y) {
    return y==CENTER?0x52aa:((x-X)%56==0 || (y-Y)%40==0)?GRID:0;
}
static void setup(unsigned slot) {
    volatile uint16_t *fb=hal_video_frame(slot);
    fill(fb,0,0,480,272,BG);
    text(fb,12,8,"I2S MICROPHONE",2,CYAN,BG);
    text(fb,12,32,"46875 samples/s  24-bit  512 points / 10.9 ms",1,WHITE,BG);
    text(fb,12,46,"Speak or clap. A:auto  +/-:gain  Space:hold  L/R:channel",1,WHITE,BG);
    for(unsigned y=Y;y<Y+H;++y) {
        for(unsigned x=X;x<X+W;++x)fb[y*480+x]=background_pixel(x,y);
        if(!(y&7u))hal_poll();
    }
    for(unsigned x=X-1;x<=X+W;++x) {fb[(Y-1)*480+x]=WHITE;fb[(Y+H)*480+x]=WHITE;}
    for(unsigned y=Y-1;y<=Y+H;++y) {fb[y*480+X-1]=WHITE;fb[y*480+X+W]=WHITE;}
}
static void append(char *line,unsigned *count,const char *s) {while(*s && *count<76)line[(*count)++]=*s++;line[*count]=0;}
static void number(char *line,unsigned *count,int32_t v) {
    if(v<0) {append(line,count,"-");v=-v;}
    char reversed[10];unsigned n=0;
    do {reversed[n++]=(char)('0'+v%10);v/=10;}while(v);
    while(n && *count<76)line[(*count)++]=reversed[--n];
    line[*count]=0;
}
static void statistics(void) {
    int64_t total=0;minimum=8388607;maximum=-8388608;
    for(unsigned i=0;i<N;++i) {
        int32_t v=samples[i];total+=v;if(v<minimum)minimum=v;if(v>maximum)maximum=v;
    }
    mean=(int32_t)(total/N);peak=0;
    for(unsigned i=0;i<N;++i) {int32_t v=samples[i]-mean;unsigned a=(unsigned)(v<0?-v:v);if(a>peak)peak=a;}
    draw_shift=gain_shift;
    if(automatic) {draw_shift=0;while((peak>>draw_shift)>H/2-4)++draw_shift;}
}
static int acquire(void) {
    if(hal_mic_capture()!=HAL_OK)return 0;
    hal_mic_info_t info;uint32_t start=hal_time_ms();
    do {hal_poll();hal_mic_get_info(&info);}while(!info.done && hal_time_ms()-start<100);
    unsigned count=0;
    if(!info.done || info.overruns || hal_mic_read(samples,N,&count)!=HAL_OK || count!=N)return 0;
    statistics();++windows;return 1;
}
static void resume(void) {
    if(!(mic_control_read()&1)) {hal_mic_start(right);hal_delay_ms(200);}
    paused=0;dirty=1;
}
static void drain_usb(void) {
    hal_usb_key_t key;hal_usb_mouse_t mouse;hal_usb_report_t raw;
    while(hal_usb_key_take(&key)==HAL_OK) {
        if(!key.pressed)continue;
        dirty=1;
        if(key.usage==44) {if(paused)resume();else paused=1;}
        if(key.usage==4)automatic^=1;
        if(key.usage==46 && gain_shift) {--gain_shift;automatic=0;}
        if(key.usage==45 && gain_shift<23) {++gain_shift;automatic=0;}
        if(key.usage==15 || key.usage==21) {right=key.usage==21;hal_mic_start(right);hal_delay_ms(200);}
    }
    while(hal_usb_mouse_take(&mouse)==HAL_OK) {}
    while(hal_usb_report_take(&raw)==HAL_OK) {}
}
static void waveform(void) {
    unsigned slot=active^1u;volatile uint16_t *fb=hal_video_frame(slot);
    // Restore only the previous trace, then draw vertical min/max segments.
    for(unsigned x=0;x<W;++x) {
        if(drawn[slot]) {
            int first=old_y[slot][x],last=x?old_y[slot][x-1]:first;
            if(first>last) {int swap=first;first=last;last=swap;}
            for(int y=first;y<=last;++y)fb[y*480+X+x]=background_pixel(X+x,(unsigned)y);
        }
    }
    for(unsigned x=0;x<W;++x) {
        int y=CENTER-((samples[x*N/W]-mean)>>draw_shift);
        if(y<Y)y=Y;
        if(y>=Y+H)y=Y+H-1;
        int first=y,last=x?old_y[slot][x-1]:y;old_y[slot][x]=y;
        if(first>last) {int swap=first;first=last;last=swap;}
        for(int at=first;at<=last;++at)fb[at*480+X+x]=GREEN;
        if(!(x&31u)) {hal_poll();drain_usb();}
    }
    drawn[slot]=1;
    fill(fb,12,232,456,32,BG);
    char line[77];unsigned n=0;line[0]=0;
    append(line,&n,right?"RIGHT":"LEFT");append(line,&n,paused?" HOLD":" LIVE");append(line,&n,automatic?" AUTO":" MANUAL");
    append(line,&n," SHIFT:");number(line,&n,(int32_t)draw_shift);append(line,&n," PEAK:");number(line,&n,(int32_t)peak);
    append(line,&n," P-P:");number(line,&n,maximum-minimum);text(fb,12,234,line,1,YELLOW,BG);
    n=0;line[0]=0;append(line,&n,"WINDOWS:");number(line,&n,(int32_t)windows);append(line,&n," ERR:");number(line,&n,(int32_t)errors);
    append(line,&n," DA HIGH:");number(line,&n,(int32_t)mic_activity_read());
    append(line,&n," LCD UNDER:");number(line,&n,(int32_t)rgb_lcd_underflows_read());text(fb,12,250,line,1,CYAN,BG);
    if(hal_video_present(slot)==HAL_OK)active=slot;else ++errors;
}
static void status(void) {
    hal_mic_info_t mic;hal_mic_get_info(&mic);hal_usb_info_t usb;hal_usb_get_info(&usb);
    hal_uart_puts("MIC rate=");hal_uart_hex(mic.sample_rate);hal_uart_puts(" control=");hal_uart_hex(mic.control);
    hal_uart_puts(" samples=");hal_uart_hex(mic.samples);hal_uart_puts(" level=");hal_uart_hex(mic.level);
    hal_uart_puts(" overruns=");hal_uart_hex(mic.overruns);hal_uart_puts(" windows=");hal_uart_hex(windows);
    hal_uart_puts(" DA_HIGH_SEEN=");hal_uart_hex(mic.activity);
    hal_uart_puts(" errors=");hal_uart_hex(errors);hal_uart_puts(" shift=");hal_uart_hex(draw_shift);
    hal_uart_puts("\r\nMIC min=");hal_uart_hex((uint32_t)minimum);hal_uart_puts(" max=");hal_uart_hex((uint32_t)maximum);
    hal_uart_puts(" mean=");hal_uart_hex((uint32_t)mean);hal_uart_puts(" peak=");hal_uart_hex(peak);
    hal_uart_puts("\r\nUSB errors=");hal_uart_hex(usb.errors);hal_uart_puts(" drops=");hal_uart_hex(usb.key_drops+usb.mouse_drops+usb.report_drops);
    hal_uart_puts("\r\n");hal_video_status();
}
static int equal(const char *a,const char *b) {while(*a && *a==*b) {++a;++b;}return *a==*b;}
static void command(const char *s) {
    if(equal(s,"status"))status();
    else if(equal(s,"test mic")) {
        unsigned before=rgb_lcd_completed_read();int ok=acquire();hal_delay_ms(40);
        hal_mic_info_t mic;hal_mic_get_info(&mic);
        ok=ok && (mic.control&1) && mic.captured==N && mic.level==0 && !mic.overruns && !errors
            && minimum>=-8388608 && maximum<=8388607 && peak>0
            && rgb_lcd_completed_read()!=before && !rgb_lcd_underflows_read();
        status();hal_uart_puts(ok?"TEST mic PASS\r\n":"TEST mic FAIL\r\n");
    } else if(equal(s,"dump")) {
        hal_uart_puts("PCM24 BEGIN\r\n");
        for(unsigned i=0;i<N;++i) {hal_uart_hex((uint32_t)samples[i]);hal_uart_puts("\r\n");if(!(i&31u)) {hal_poll();drain_usb();}}
        hal_uart_puts("PCM24 END\r\n");
    } else if(equal(s,"left") || equal(s,"right")) {
        right=equal(s,"right");hal_mic_start(right);hal_delay_ms(200);
    } else if(equal(s,"hold")) {paused=1;dirty=1;}
    else if(equal(s,"stop")) {hal_mic_stop();paused=1;dirty=1;}
    else if(equal(s,"run"))resume();
    else if(equal(s,"reboot"))hal_reboot();
    else hal_uart_puts("Commands: status, test mic, dump, left, right, hold, stop, run, reboot (!)\r\n");
    hal_uart_puts("> ");
}
int main(void) {
    hal_init();
    if(hal_video_init()!=HAL_OK || hal_mic_start(0)!=HAL_OK) {
        hal_uart_puts("MIC DEMO INIT FAIL\r\n");for(;;)if(hal_uart_getc()=='!')hal_reboot();
    }
    setup(1);hal_video_present(1);active=1;setup(0);hal_delay_ms(200);
    hal_uart_puts("MIC WAVEFORM DEMO | I2S 3MHz / 46875Hz / signed24\r\nSYSTEM READY\r\n> ");
    char line[40];unsigned length=0;uint32_t next=hal_time_ms();
    for(;;) {
        hal_poll();drain_usb();int ch=hal_uart_getc();if(ch=='!')hal_reboot();
        if(ch=='\r' || ch=='\n') {if(length) {line[length]=0;command(line);length=0;}}
        else if(ch>=32 && ch<127 && length<sizeof(line)-1)line[length++]=(char)ch;
        if(!paused && hal_deadline_reached(hal_time_ms(),next)) {
            if(acquire())waveform();else ++errors;
            next=hal_time_ms()+33;
        }
        if(paused && dirty && windows) {statistics();dirty=0;waveform();}
    }
}
