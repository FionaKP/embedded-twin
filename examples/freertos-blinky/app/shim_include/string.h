/* Freestanding stub declaring the symbols libc_shim.c provides. */
#ifndef _SHIM_STRING_H
#define _SHIM_STRING_H
#include <stddef.h>
void *memset(void *dst, int c, size_t n);
void *memcpy(void *dst, const void *src, size_t n);
void *memmove(void *dst, const void *src, size_t n);
size_t strlen(const char *s);
char *strcpy(char *dst, const char *src);
#endif
