/* BIOS payload shim: soft double math (rv32imaf: single in HW, double via
 * libgcc soft helpers). Only the handful DOOM actually calls are defined. */
#ifndef SHIM_MATH_H
#define SHIM_MATH_H
double fabs(double);
double floor(double);
double ceil(double);
double fmod(double,double);
double sqrt(double);
#define M_PI 3.14159265358979323846
#endif
