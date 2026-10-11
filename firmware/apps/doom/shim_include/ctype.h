/* BIOS payload shim: character classification as macros. */
#ifndef SHIM_CTYPE_H
#define SHIM_CTYPE_H
#define isdigit(c)  ((c)>='0'&&(c)<='9')
#define isxdigit(c) (((c)>='0'&&(c)<='9')||((c)>='a'&&(c)<='f')||((c)>='A'&&(c)<='F'))
#define isalpha(c)  (((c)>='a'&&(c)<='z')||((c)>='A'&&(c)<='Z'))
#define isalnum(c)  (isalpha(c)||isdigit(c))
#define isspace(c)  ((c)==' '||(c)=='\t'||(c)=='\n'||(c)=='\r'||(c)=='\f'||(c)=='\v')
#define isupper(c)  ((c)>='A'&&(c)<='Z')
#define islower(c)  ((c)>='a'&&(c)<='z')
#define isprint(c)  ((c)>=' '&&(c)<127)
#define iscntrl(c)  ((unsigned)(c)<32||(c)==127)
#define ispunct(c)  (isprint(c)&&!isalnum(c)&&(c)!=' ')
#define isgraph(c)  (isprint(c)&&(c)!=' ')
#define tolower(c)  (isupper(c)?(c)+32:(c))
#define toupper(c)  (islower(c)?(c)-32:(c))
#endif
