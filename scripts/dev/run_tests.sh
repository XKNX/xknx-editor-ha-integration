#!/bin/sh
# The tests in the environment of setup_tests.sh; arguments go to pytest.
cd "$(dirname "$0")/../.." || exit 1
exec /root/tests/bin/python -m pytest -q -p no:cacheprovider "$@"
