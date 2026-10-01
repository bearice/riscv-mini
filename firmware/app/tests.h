#pragma once
/* DDR-only monitor acceptance commands. No test runs automatically at boot. */
void tests_help(void);
void tests_poll(void);
int tests_command(const char *command);
