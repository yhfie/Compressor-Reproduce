#!/bin/bash
if [[ "$@" == *"-c"* ]]; then
    exec /usr/bin/g++ "$@"
else
    exec /usr/bin/g++ -shared "$@"
fi
