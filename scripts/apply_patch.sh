#!/bin/bash

echo "Applying critical patches..."

# replace redis keys
grep -rl "Deriv_EngineMode" . | xargs sed -i 's/Deriv_EngineMode/Deriv_EngineMode/g'
grep -rl "Deriv_ActiveSymbols" . | xargs sed -i 's/Deriv_ActiveSymbols/Deriv_ActiveSymbols/g'

echo "Patch applied."
