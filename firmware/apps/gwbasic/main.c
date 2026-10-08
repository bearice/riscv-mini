/* main.c - the payload entry point.
 *
 * The BIOS calls payload_main(const struct bios_info *) after copying the image
 * to RAM and clearing BSS.  Hand the description to the platform layer, arm the
 * SYSTEM exit, and run the REPL.  Returning from payload_main is what SYSTEM
 * does: the BIOS prints "PAYLOAD RETURNED" and resumes control. */
#include "basic.h"
#include "../../bios/include/bios.h"

/* Defined in plat_bios.c: returns 0 the first time and 1 after SYSTEM. */

int payload_main(const struct bios_info *info)
{
    bas_plat_set_info(info);
    for (;;) {
        if (bas_plat_exit_arm() != 0)
            break;                /* SYSTEM asked to leave the interpreter */
        bas_banner();
        bas_repl();
    }
    return 0;
}
