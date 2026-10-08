/* Host regression for the BIOS VT100/ANSI terminal core (firmware/bios/vt.c).
 * The core is deliberately HAL-free, so it runs natively:
 *
 *   cc -Wall -Wextra -I firmware/bios tests/vt_console_test.c \
 *      firmware/bios/vt.c -o vt-console-test && ./vt-console-test
 *
 * Scope: parser state machine, grid contents and attributes, scrolling
 * regions, alternate screen and the colour mapping. It does NOT exercise the
 * UART/LCD fan-out (tests/console_output_test.c covers that) and no physical
 * hardware is involved. */
#include <stdio.h>
#include <string.h>
#include "vt.h"

static vt_t t;
static char report[64];
static unsigned checks,failures;

static void capture(void *ctx,const char *text) {
    (void)ctx;
    strncpy(report,text,sizeof report-1);
    report[sizeof report-1]=0;
}

static void reset(void) {vt_reset(&t);t.report=capture;report[0]=0;}
static void feed(const char *s) {while(*s)vt_putc(&t,(unsigned char)*s++);}

static void row(unsigned r,char *out) {
    int c;
    for(c=0;c<VT_COLS;++c)out[c]=t.grid[r][c].ch;
    out[VT_COLS]=0;
    while(c>0 && out[c-1]==' ')out[--c]=0;
}

static void check(const char *what,int ok) {
    ++checks;
    if(!ok) {++failures;printf("FAIL %s\n",what);}
}

static void check_row(const char *what,unsigned r,const char *expected) {
    char buf[VT_COLS+1];
    row(r,buf);
    ++checks;
    if(strcmp(buf,expected)) {++failures;printf("FAIL %s: row %u = \"%s\", expected \"%s\"\n",what,r,buf,expected);}
}

static void check_cur(const char *what,unsigned cx,unsigned cy) {
    ++checks;
    if(t.cx!=cx || t.cy!=cy) {++failures;printf("FAIL %s: cursor=(%u,%u), expected (%u,%u)\n",what,t.cx,t.cy,cx,cy);}
}

static void check_blank(const char *what,unsigned r0,unsigned c0,unsigned r1,unsigned c1) {
    for(unsigned r=r0;r<=r1;++r)for(unsigned c=c0;c<=c1;++c)
        if(t.grid[r][c].ch!=' ') {++checks;++failures;printf("FAIL %s: (%u,%u)='%c'\n",what,r,c,t.grid[r][c].ch);return;}
    ++checks;
}

int main(void) {
    uint16_t fg,bg;

    /* --- plain text, CR/LF and wrap beyond the last column --- */
    reset();
    feed("BIOS");
    check_row("plain text",0,"BIOS");
    check_cur("plain text cursor",4,0);
    feed("\r\nnext");
    check_row("CRLF advances",1,"next");
    reset();
    for(unsigned i=0;i<VT_COLS;++i)vt_putc(&t,'x');
    check_cur("wrap holds at last column",VT_COLS-1,0);
    vt_putc(&t,'y');
    check_cur("wrap moves to next row",1,1);

    /* --- CUP / CUU / CUD / CUF / CUB / CHA / VPA and clamping --- */
    reset();
    feed("\033[5;10H");
    check_cur("CUP 5;10",9,4);
    feed("\033[3A");
    check_cur("CUU",9,1);
    feed("\033[99B");
    check_cur("CUD clamps to last row",9,VT_ROWS-1);
    feed("\033[2;2H\033[5C");
    check_cur("CUF",6,1);
    feed("\033[999D");
    check_cur("CUB clamps at column 0",0,1);
    feed("\033[12G");
    check_cur("CHA",11,1);
    feed("\033[7d");
    check_cur("VPA",11,6);
    feed("\033[H");
    check_cur("CUP default is home",0,0);

    /* --- ED / EL --- */
    reset();
    for(unsigned r=0;r<3;++r) {feed("AAAA\r\n");}
    feed("\033[1;3H\033[J");
    check_blank("ED 0 erases from cursor",1,2,2,VT_COLS-1);
    check_row("ED 0 keeps the head",1,"AA");
    reset();
    for(unsigned r=0;r<3;++r) {feed("AAAA\r\n");}
    feed("\033[2;3H\033[1J");
    check_blank("ED 1 erases up to cursor",0,0,1,2);
    check_row("ED 1 keeps the tail",1,"   A");
    reset();
    feed("hello\033[2J");
    check_blank("ED 2 erases the whole screen",0,0,VT_ROWS-1,VT_COLS-1);
    reset();
    feed("abcdef\033[1;3H\033[K");
    check_row("EL 0 erases to end of line",0,"ab");
    reset();
    feed("abcdef\033[1;3H\033[1K");
    check_row("EL 1 erases from line start",0,"   def");
    reset();
    feed("abcdef\033[1;3H\033[2K");
    check_row("EL 2 erases the whole line",0,"");

    /* --- the GW-BASIC CLS sequence now actually clears --- */
    reset();
    feed("junk\033[2J\033[H");
    check_blank("gwbasic CLS",0,0,VT_ROWS-1,VT_COLS-1);
    check_cur("gwbasic CLS homes",0,0);

    /* --- SGR attributes --- */
    reset();
    feed("\033[1;31;44mX");
    check("SGR sets bold",(t.grid[0][0].attr & ATTR_BOLD)!=0);
    check("SGR sets fg 1",((t.grid[0][0].attr >> ATTR_FG_SHIFT) & 15u)==1);
    check("SGR sets bg 4",((t.grid[0][0].attr >> ATTR_BG_SHIFT) & 15u)==4);
    feed("\033[22;39;49mY");
    check("SGR 22/39/49 clears",(t.grid[0][1].attr & ATTR_BOLD)==0 &&
          ((t.grid[0][1].attr >> ATTR_FG_SHIFT) & 15u)==ATTR_FG_DEFAULT &&
          ((t.grid[0][1].attr >> ATTR_BG_SHIFT) & 15u)==ATTR_BG_DEFAULT);
    feed("\033[7mR\033[27mN");
    check("SGR 7 sets reverse",(t.grid[0][2].attr & ATTR_REVERSE)!=0);
    check("SGR 27 clears reverse",(t.grid[0][3].attr & ATTR_REVERSE)==0);
    feed("\033[0mZ");
    check("SGR 0 resets",t.grid[0][4].attr==((ATTR_FG_DEFAULT<<ATTR_FG_SHIFT)|(ATTR_BG_DEFAULT<<ATTR_BG_SHIFT)));
    feed("\033[91;104mB");
    check("SGR bright fg 9 / bg 12",((t.grid[0][5].attr >> ATTR_FG_SHIFT) & 15u)==9 &&
          ((t.grid[0][5].attr >> ATTR_BG_SHIFT) & 15u)==12);

    /* --- 默认前景必须是白色：ANSI 调色板里索引 1 是红，默认前景曾误用 1 --- */
    reset();
    vt_colors(&t.grid[0][0],&fg,&bg);
    check("default foreground is white 0xffff",fg==0xffff);
    check("default background is black",bg==0x0000);

    /* --- colour mapping: reverse swaps, bold brightens only 1..7 --- */
    {vt_cell_t c;
     c.ch='x';c.attr=(vt_attr_t)((1u<<ATTR_FG_SHIFT)|(4u<<ATTR_BG_SHIFT));     vt_colors(&c,&fg,&bg);
     check("vt_colors plain fg/bg",fg==vt_rgb(1)&&bg==vt_rgb(4));
     c.attr|=ATTR_REVERSE;
     vt_colors(&c,&fg,&bg);
     check("vt_colors reverse swaps",fg==vt_rgb(4)&&bg==vt_rgb(1));
     c.attr=(vt_attr_t)((2u<<ATTR_FG_SHIFT)|ATTR_BOLD);
     vt_colors(&c,&fg,&bg);
     check("vt_colors bold brightens 2 -> 10",fg==vt_rgb(10));
     c.attr=(vt_attr_t)((9u<<ATTR_FG_SHIFT)|ATTR_BOLD);
     vt_colors(&c,&fg,&bg);
     check("vt_colors bold leaves 9..15 alone",fg==vt_rgb(9));}

    /* --- scrolling region (DECSTBM) --- */
    reset();
    for(unsigned r=0;r<VT_ROWS;++r) {feed("row\r\n");}   /* row r holds "row" */
    feed("\033[3;6r");                                   /* region rows 3..6 (1-based) */
    check_cur("DECSTBM homes to region top",0,2);
    check("DECSTBM bounds",t.top==2&&t.bottom==5);
    feed("\033[6;1H\033[L");                              /* IL at region bottom */
    check_row("IL inside region keeps row 0",0,"row");
    check_blank("IL clears the inserted line",5,0,5,VT_COLS-1);
    feed("\033[3;1H\033[M");                              /* DL at region top */
    check_row("DL shifts the region up",2,"row");
    check_row("DL leaves outside rows alone",0,"row");
    /* LF at the region bottom scrolls only the region */
    reset();
    for(unsigned r=0;r<6;++r) {feed("L\r\n");}
    feed("\033[2;4r\033[4;1H\n");
    check_row("region scroll keeps row 0",0,"L");
    check_row("region scroll keeps row 5",5,"L");
    check_blank("region scroll clears the last region row",3,0,3,VT_COLS-1);

    /* --- ICH / DCH --- */
    reset();
    feed("abcdef\033[1;3H\033[2@");
    check_row("ICH inserts blanks",0,"ab  cdef");
    reset();
    feed("abcdef\033[1;3H\033[2P");
    check_row("DCH deletes characters",0,"abef");

    /* --- ECH (X) --- */
    reset();
    feed("abcdef\033[1;3H\033[2X");
    check_row("ECH erases in place",0,"ab  ef");

    /* --- alternate screen --- */
    reset();
    feed("main");
    feed("\033[?1049h");
    check("alt screen selected",t.grid==t.alt);
    check_cur("alt screen homes",0,0);
    check_blank("alt screen starts blank",0,0,VT_ROWS-1,VT_COLS-1);
    feed("alt");
    check_row("alt screen takes writes",0,"alt");
    feed("\033[?1049l");
    check("main screen restored",t.grid==t.main);
    check_row("main screen content survives",0,"main");

    /* --- modes and reports --- */
    reset();
    check("APPKEYS off by default",(t.mode&VT_MODE_APPKEYS)==0);
    feed("\033[?1h");
    check("DECCKM sets APPKEYS",(t.mode&VT_MODE_APPKEYS)!=0);
    feed("\033[?1l");
    check("DECCKM clears APPKEYS",(t.mode&VT_MODE_APPKEYS)==0);
    reset();
    feed("\033[3;7H\033[6n");
    check("DSR 6 reports the cursor",strcmp(report,"\033[3;7R")==0);
    feed("\033[5n");
    check("DSR 5 reports ready",strcmp(report,"\033[0n")==0);

    /* --- save/restore, tab stops, RI --- */
    reset();
    feed("\033[4;9H\0337\033[1;1H\0338");
    check_cur("ESC 7/8 save and restore",8,3);
    reset();
    feed("\t");
    check_cur("HT reaches the first tab stop",8,0);
    feed("\t");
    check_cur("HT reaches the second tab stop",16,0);
    feed("\033[g");
    feed("\033[1G\t");
    check_cur("TBC 0 clears all stops",VT_COLS-1,0);
    reset();
    feed("\033[3;1H\033M");
    check_cur("ESC M reverses into the row above",0,1);

    /* --- unknown and malformed sequences must be swallowed, not printed --- */
    reset();
    feed("a\033[1tb\033[38;5;196mc\033[?2004hd");
    check_row("unknown CSI swallowed",0,"abcd");
    reset();
    feed("a\033");
    check_row("lone ESC is buffered",0,"a");
    feed("b");
    check_row("lone ESC swallows one byte",0,"a");
    reset();
    feed("a\033[");
    check_row("unterminated CSI holds text",0,"a");
    feed("12");
    check_row("unterminated CSI keeps buffering",0,"a");
    feed("Hb");
    check_row("unterminated CSI completes",0,"a");
    check_row("unterminated CSI applied the move",11,"b");
    reset();
    feed("a\033(Bb");
    check_row("charset selection is swallowed",0,"ab");

    /* --- C0 controls --- */
    reset();
    feed("ab\010c");
    check_row("backspace",0,"ac");
    feed("\007");
    check_row("BEL prints nothing",0,"ac");

    printf("%u checks, %u failures\n",checks,failures);
    return failures!=0;
}
