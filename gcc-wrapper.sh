#!/bin/bash
# Wrapper for gcc to force -shared during linking

# Inject -shared only when linking (i.e., not compiling .c to .o)
if [[ "$@" == *"-c"* ]]; then
    exec /usr/bin/gcc "$@"
else
    exec /usr/bin/gcc -shared "$@"
fi
