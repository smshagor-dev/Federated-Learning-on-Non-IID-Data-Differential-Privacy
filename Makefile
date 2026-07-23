PYTHON ?= python

.PHONY: test-baseline cpp-configure cpp-build cpp-test

test-baseline:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

cpp-configure:
	cmake -S cpp -B build/cpp

cpp-build:
	cmake --build build/cpp

cpp-test:
	ctest --test-dir build/cpp --output-on-failure
