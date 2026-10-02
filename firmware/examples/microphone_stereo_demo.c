/* Two physical microphones, synchronized PCM24 pairs and LCD waveforms. */
#include <hal/hal.h>
#include <generated/csr.h>
#include <generated/soc.h>
#include "rgb_canvas.h"
enum {BG=0x1084,GRID=0x2945,WHITE=0xffff,GREEN=0x07e0,CYAN=0x07ff,YELLOW=0xffe0};
enum {X=16,W=448,H=72,N=512};
static const unsigned top[2]={62,155},color[2]={GREEN,CYAN};
static hal_mic_pair_t samples[N];
static struct {int32_t minimum,maximum,mean;unsigned peak;} stats[2];
static unsigned active,windows,errors,paused,dirty,automatic=1,gain_shift=8,draw_shift=8,different;
static int old_y[2][2][W];static unsigned drawn[2];
enum {CAPTURE,READ,STATS,DRAW,PRESENT,STAGES};
static uint32_t costs[STAGES],setup_cycles[2];
static struct {unsigned remaining,count;uint32_t first,last,minimum[STAGES],maximum[STAGES];uint64_t sum[STAGES];} perf;
static int32_t sample(unsigned i,unsigned channel) {return channel?samples[i].right:samples[i].left;}
static uint16_t background_pixel(unsigned x,unsigned y,unsigned channel) {
    return y==top[channel]+H/2?0x52aa:((x-X)%56==0 || (y-top[channel])%18==0)?GRID:0;
}
static void setup(unsigned slot) {
    volatile uint16_t *fb=hal_video_frame(slot);
    fill(fb,0,0,480,272,BG);text(fb,12,8,"STEREO I2S MICROPHONES",2,CYAN,BG);
    text(fb,12,32,"46875Hz / 24bit  A:auto  +/-:gain  Space:hold",1,WHITE,BG);
    for(unsigned c=0;c<2;++c) {
        for(unsigned y=top[c];y<top[c]+H;++y) {
            for(unsigned x=X;x<X+W;++x)fb[y*480+x]=background_pixel(x,y,c);
            if(!(y&7))hal_poll();
        }
        for(unsigned x=X-1;x<=X+W;++x) {fb[(top[c]-1)*480+x]=WHITE;fb[(top[c]+H)*480+x]=WHITE;}
        for(unsigned y=top[c]-1;y<=top[c]+H;++y) {fb[y*480+X-1]=WHITE;fb[y*480+X+W]=WHITE;}
    }
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
    unsigned peak=0;different=0;
    for(unsigned i=0;i<N;++i)if(samples[i].left!=samples[i].right)++different;
    for(unsigned c=0;c<2;++c) {
        int64_t total=0;stats[c].minimum=8388607;stats[c].maximum=-8388608;
        for(unsigned i=0;i<N;++i) {
            int32_t v=sample(i,c);total+=v;
            if(v<stats[c].minimum)stats[c].minimum=v;
            if(v>stats[c].maximum)stats[c].maximum=v;
        }
        stats[c].mean=(int32_t)(total/N);stats[c].peak=0;
        for(unsigned i=0;i<N;++i) {
            int32_t v=sample(i,c)-stats[c].mean;unsigned a=(unsigned)(v<0?-v:v);
            if(a>stats[c].peak)stats[c].peak=a;
        }
        if(stats[c].peak>peak)peak=stats[c].peak;
    }
    // Shared gain preserves the relative amplitude of the two microphones.
    draw_shift=gain_shift;
    if(automatic) {draw_shift=0;while((peak>>draw_shift)>H/2-4)++draw_shift;}
}
static int acquire(void) {
    uint32_t ticks=hal_ticks();
    if(hal_mic_capture()!=HAL_OK)return 0;
    hal_mic_info_t info;uint32_t start=hal_time_ms();
    do {hal_poll();hal_mic_get_info(&info);}while(!info.done && hal_time_ms()-start<100);
    costs[CAPTURE]=hal_ticks()-ticks;ticks=hal_ticks();
    unsigned count=0;
    if(!info.done || info.overruns || hal_mic_read_stereo(samples,N,&count)!=HAL_OK || count!=N)return 0;
    costs[READ]=hal_ticks()-ticks;ticks=hal_ticks();
    statistics();costs[STATS]=hal_ticks()-ticks;++windows;return 1;
}
static void resume(void) {
    if(!(mic_control_read()&1)) {hal_mic_start_stereo();hal_delay_ms(200);}
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
    }
    while(hal_usb_mouse_take(&mouse)==HAL_OK) {}
    while(hal_usb_report_take(&raw)==HAL_OK) {}
}
static void waveform(void) {
    uint32_t ticks=hal_ticks();
    unsigned slot=active^1u;volatile uint16_t *fb=hal_video_frame(slot);
    for(unsigned c=0;c<2;++c) {
        for(unsigned x=0;x<W;++x) {
            if(drawn[slot]) {
                int first=old_y[slot][c][x],last=x?old_y[slot][c][x-1]:first;
                if(first>last) {int swap=first;first=last;last=swap;}
                for(int y=first;y<=last;++y)fb[y*480+X+x]=background_pixel(X+x,(unsigned)y,c);
            }
        }
        for(unsigned x=0;x<W;++x) {
            int y=(int)top[c]+H/2-((sample(x*N/W,c)-stats[c].mean)>>draw_shift);
            if(y<(int)top[c])y=(int)top[c];
            if(y>=(int)top[c]+H)y=(int)top[c]+H-1;
            int first=y,last=x?old_y[slot][c][x-1]:y;old_y[slot][c][x]=y;
            if(first>last) {int swap=first;first=last;last=swap;}
            for(int at=first;at<=last;++at)fb[at*480+X+x]=color[c];
            if(!(x&31)) {hal_poll();drain_usb();}
        }
        fill(fb,12,top[c]-14,456,10,BG);char line[77];unsigned n=0;line[0]=0;
        append(line,&n,c?"R T6/R8":"L P11/R11");append(line,&n," PEAK:");number(line,&n,(int32_t)stats[c].peak);
        append(line,&n," P-P:");number(line,&n,stats[c].maximum-stats[c].minimum);
        text(fb,12,top[c]-14,line,1,color[c],BG);
    }
    drawn[slot]=1;fill(fb,12,235,456,29,BG);char line[77];unsigned n=0;line[0]=0;
    append(line,&n,paused?"HOLD":"LIVE");append(line,&n,automatic?" AUTO SHIFT:":" MANUAL SHIFT:");number(line,&n,(int32_t)draw_shift);
    append(line,&n," WINDOWS:");number(line,&n,(int32_t)windows);append(line,&n," ERR:");number(line,&n,(int32_t)errors);
    text(fb,12,235,line,1,YELLOW,BG);n=0;line[0]=0;
    append(line,&n,"DA L/R:");number(line,&n,(int32_t)mic_activity_read());append(line,&n,"/");number(line,&n,(int32_t)mic_activity_right_read());
    append(line,&n," DIFFERENT:");number(line,&n,(int32_t)different);append(line,&n," LCD UNDER:");number(line,&n,(int32_t)rgb_lcd_underflows_read());
    text(fb,12,250,line,1,CYAN,BG);
    costs[DRAW]=hal_ticks()-ticks;ticks=hal_ticks();
    if(hal_video_present(slot)==HAL_OK)active=slot;else ++errors;
    costs[PRESENT]=hal_ticks()-ticks;
}
static void perf_value(const char *name,uint32_t v) {hal_uart_puts(name);hal_uart_hex(v);}
static void perf_record(uint32_t start) {
    if(!perf.remaining)return;
    if(!perf.count)perf.first=start;
    perf.last=start;++perf.count;
    for(unsigned i=0;i<STAGES;++i) {
        perf.sum[i]+=costs[i];
        if(costs[i]<perf.minimum[i])perf.minimum[i]=costs[i];
        if(costs[i]>perf.maximum[i])perf.maximum[i]=costs[i];
    }
    if(--perf.remaining)return;
    static const char *names[]={"capture","read","stats","draw","present"};
    perf_value("PERF clock_hz=",CONFIG_CLOCK_FREQUENCY);perf_value(" frames=",perf.count);
    perf_value(" elapsed_cycles=",perf.last-perf.first);hal_uart_puts("\r\n");
    for(unsigned i=0;i<STAGES;++i) {
        hal_uart_puts(names[i]);perf_value(" avg_cycles=",(uint32_t)(perf.sum[i]/perf.count));
        perf_value(" min_cycles=",perf.minimum[i]);perf_value(" max_cycles=",perf.maximum[i]);hal_uart_puts("\r\n");
    }
    perf_value("setup0_cycles=",setup_cycles[0]);perf_value(" setup1_cycles=",setup_cycles[1]);
    hal_uart_puts("\r\nPERF DONE\r\n> ");
}
static void status(void) {
    hal_mic_info_t mic;hal_mic_get_info(&mic);hal_usb_info_t usb;hal_usb_get_info(&usb);
    hal_uart_puts("STEREO rate=");hal_uart_hex(mic.sample_rate);hal_uart_puts(" control=");hal_uart_hex(mic.control);
    hal_uart_puts(" pairs=");hal_uart_hex(mic.samples);hal_uart_puts(" level=");hal_uart_hex(mic.level);
    hal_uart_puts(" overruns=");hal_uart_hex(mic.overruns);hal_uart_puts(" windows=");hal_uart_hex(windows);
    hal_uart_puts(" errors=");hal_uart_hex(errors);hal_uart_puts(" different=");hal_uart_hex(different);
    for(unsigned c=0;c<2;++c) {
        hal_uart_puts(c?"\r\nR T6/R8":"\r\nL P11/R11");hal_uart_puts(" min=");hal_uart_hex((uint32_t)stats[c].minimum);
        hal_uart_puts(" max=");hal_uart_hex((uint32_t)stats[c].maximum);hal_uart_puts(" mean=");hal_uart_hex((uint32_t)stats[c].mean);
        hal_uart_puts(" peak=");hal_uart_hex(stats[c].peak);hal_uart_puts(" DA_HIGH_SEEN=");hal_uart_hex(c?mic.activity_right:mic.activity);
    }
    hal_uart_puts("\r\nUSB errors=");hal_uart_hex(usb.errors);hal_uart_puts(" drops=");hal_uart_hex(usb.key_drops+usb.mouse_drops+usb.report_drops);
    hal_uart_puts("\r\n");hal_video_status();
}
static int equal(const char *a,const char *b) {while(*a && *a==*b) {++a;++b;}return *a==*b;}
static void command(const char *s) {
    if(equal(s,"status"))status();
    else if(equal(s,"perf")) {
        resume();perf.count=0;perf.remaining=64;
        for(unsigned i=0;i<STAGES;++i) {perf.sum[i]=0;perf.minimum[i]=~0u;perf.maximum[i]=0;}
        hal_uart_puts("PERF START: 64 live frames, cycles at 60MHz; no UART output during sampling\r\n");
    }
    else if(equal(s,"test mic") || equal(s,"test mic stereo")) {
        unsigned before=rgb_lcd_completed_read();int ok=acquire();hal_delay_ms(40);
        hal_mic_info_t mic;hal_mic_get_info(&mic);
        ok=ok && mic.control==5 && mic.captured==N && !mic.level && !mic.overruns && !errors && different
            && mic.activity && mic.activity_right && rgb_lcd_completed_read()!=before && !rgb_lcd_underflows_read();
        for(unsigned c=0;c<2;++c)ok=ok && stats[c].minimum>=-8388608 && stats[c].maximum<=8388607 && stats[c].peak>0;
        status();hal_uart_puts(ok?"TEST mic stereo PASS\r\n":"TEST mic stereo FAIL\r\n");
    } else if(equal(s,"dump")) {
        hal_uart_puts("STEREO PCM24 BEGIN L R\r\n");
        for(unsigned i=0;i<N;++i) {
            hal_uart_hex((uint32_t)samples[i].left);hal_uart_puts(" ");hal_uart_hex((uint32_t)samples[i].right);hal_uart_puts("\r\n");
            if(!(i&31)) {hal_poll();drain_usb();}
        }
        hal_uart_puts("STEREO PCM24 END\r\n");
    } else if(equal(s,"hold")) {paused=1;dirty=1;}
    else if(equal(s,"stop")) {hal_mic_stop();paused=1;dirty=1;}
    else if(equal(s,"run"))resume();
    else if(equal(s,"reboot"))hal_reboot();
    else hal_uart_puts("Commands: status, perf, test mic stereo, dump, hold, stop, run, reboot (!)\r\n");
    hal_uart_puts("> ");
}
int main(void) {
    hal_init();
    if(hal_video_init()!=HAL_OK || hal_mic_start_stereo()!=HAL_OK) {
        hal_uart_puts("MIC DEMO INIT FAIL\r\n");for(;;)if(hal_uart_getc()=='!')hal_reboot();
    }
    uint32_t ticks=hal_ticks();setup(1);setup_cycles[1]=hal_ticks()-ticks;
    hal_video_present(1);active=1;ticks=hal_ticks();setup(0);setup_cycles[0]=hal_ticks()-ticks;hal_delay_ms(200);
    hal_uart_puts("STEREO MIC DEMO | shared I2S 3MHz / 46875Hz / signed24\r\nSYSTEM READY\r\n> ");
    char line[40];unsigned length=0;uint32_t next=hal_time_ms();
    for(;;) {
        hal_poll();drain_usb();int ch=hal_uart_getc();if(ch=='!')hal_reboot();
        if(ch=='\r' || ch=='\n') {if(length) {line[length]=0;command(line);length=0;}}
        else if(ch>=32 && ch<127 && length<sizeof(line)-1)line[length++]=(char)ch;
        if(!paused && hal_deadline_reached(hal_time_ms(),next)) {
            uint32_t start=hal_ticks();
            if(acquire()) {waveform();perf_record(start);}else ++errors;
            next=hal_time_ms()+33;
        }
        if(paused && dirty && windows) {statistics();dirty=0;waveform();}
    }
}
