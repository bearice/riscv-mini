#include "ae350_bios.h"
void bios_editor(void) {
    char line[128];unsigned n=0,caret=0,overflow=0,insert=0;
    int esc_state=0;unsigned esc_value=0;
    bios_puts("> ");
    for(;;) {
        bios_poll();int c=bios_getc();if(c<0)continue;
        if(c=='\n')continue;
        // ESC [ <digits> <final> 与 ESC O <final>：分字节推进状态，避免阻塞等后续字节。
        if(esc_state) {
            if(esc_state==1) {
                if(c=='[') {esc_state=2;esc_value=0;continue;}
                if(c=='O') {esc_state=3;continue;}
                esc_state=0;continue;            // 其它 ESC 序列：忽略
            }
            if(esc_state==2) {
                if(c>='0' && c<='9') {esc_value=esc_value*10+(unsigned)(c-'0');continue;}
                if(c==';')continue;              // 参数分隔：本编辑器只用第一个参数
            }
            esc_state=0;
            if(c=='D') {if(caret)--caret;}
            else if(c=='C') {if(caret<n)++caret;}
            else if(c=='A'||c=='B')continue;      // 没有编辑历史，忽略上下键
            else if(c=='H')caret=0;
            else if(c=='F')caret=n;
            else if(c=='~') {
                if(esc_value==1||esc_value==7)caret=0;
                else if(esc_value==4||esc_value==8)caret=n;
                else if(esc_value==2)insert^=1;
                else if(esc_value==3 && caret<n) {for(unsigned i=caret;i+1<n;++i)line[i]=line[i+1];--n;}
            }
            goto redraw;
        }
        if(c==0x1b) {esc_state=1;continue;}
        if(c=='\r') {
            line[n]=0;bios_puts("\r\n");
            if(overflow)bios_puts("ERR command too long\r\n");else bios_command(line);
            n=caret=overflow=insert=0;
            bios_puts("> ");continue;
        }
        if(c==8 || c==127) {                     // 退格/Delete
            if(!caret)continue;
            if(caret==n) {--n;--caret;bios_puts("\b \b");continue;}  // 行尾：原样回显
            --caret;
            for(unsigned i=caret;i+1<n;++i)line[i]=line[i+1];
            --n;
        }
        else if(c==1)caret=0;                    // Ctrl-A
        else if(c==5)caret=n;                    // Ctrl-E
        else if(c==11) {for(unsigned i=caret;i<n;++i)line[i]=line[i+1];n=caret;}  // Ctrl-K
        else if(c==21) {n=caret=0;}              // Ctrl-U
        else if(c==23) {                         // Ctrl-W：删前一个词
            unsigned end=caret;
            while(caret && line[caret-1]==' ')--caret;
            while(caret && line[caret-1]!=' ')--caret;
            unsigned removed=end-caret;
            for(unsigned i=end;i<n;++i)line[i-removed]=line[i];
            n-=removed;
        }
        else if(c>=32 && c<127) {
            if(n>=sizeof(line)-1) {overflow=1;continue;}
            if(caret<n) {                        // 行中间：按 insert 决定插入还是覆盖
                if(insert) {                     // 插入：尾部右移，必须整行重绘
                    for(unsigned i=n;i>caret;--i)line[i]=line[i-1];
                    line[caret++]=c;++n;
                    goto redraw;
                }
                line[caret++]=c;                 // 覆盖：就地替换，长度不变
                bios_putc((char)c);continue;     // 物理光标正在 caret 处
            }
            line[caret++]=c;++n;bios_putc((char)c);continue;  // 行尾追加：原样回显
        }
        else continue;
redraw:
        bios_puts("\r\x1b[K> ");                 // CR + EL + 提示符 + 整行
        for(unsigned i=0;i<n;++i)bios_putc(line[i]);
        if(caret<n) {                            // 光标归位：CSI <n-caret> D
            unsigned back=n-caret;char digits[8];int k=0;
            do {digits[k++]=(char)('0'+back%10);back/=10;}while(back);
            bios_puts("\x1b[");
            while(k)bios_putc(digits[--k]);
            bios_putc('D');
        }
    }
}
