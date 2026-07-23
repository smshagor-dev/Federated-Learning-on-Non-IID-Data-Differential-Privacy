PYTHON ?= python

.PHONY: test-baseline proto proto-check cpp-configure cpp-build cpp-test cpp-debug cpp-release cpp-format-check cpp-tidy cpp-asan cpp-ubsan cpp-benchmark

test-baseline:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py"

proto:
	$(PYTHON) scripts/verify_proto_contracts.py
	@if command -v protoc >/dev/null 2>&1; then scripts/generate_protos.sh generated; else echo "protoc unavailable; generation skipped after compatibility verification"; fi

proto-check:
	$(PYTHON) scripts/verify_proto_contracts.py

cpp-configure:
	cmake -S cpp -B build/cpp

cpp-build:
	cmake --build build/cpp

cpp-test:
	ctest --test-dir build/cpp --output-on-failure

cpp-debug:
	cmake -S cpp -B build/cpp-debug -DCMAKE_BUILD_TYPE=Debug
	cmake --build build/cpp-debug
	ctest --test-dir build/cpp-debug --output-on-failure

cpp-release:
	cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release
	cmake --build build/cpp-release
	ctest --test-dir build/cpp-release --output-on-failure

cpp-format-check:
	clang-format --dry-run --Werror $$(find cpp -name '*.cpp' -o -name '*.hpp')

cpp-tidy:
	cmake -S cpp -B build/cpp-tidy -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
	clang-tidy $$(find cpp/core/src cpp/core/tests -name '*.cpp') -- -Icpp/core/include -std=c++20

cpp-asan:
	cmake -S cpp -B build/cpp-asan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-fsanitize=address -fno-omit-frame-pointer"
	cmake --build build/cpp-asan
	ctest --test-dir build/cpp-asan --output-on-failure

cpp-ubsan:
	cmake -S cpp -B build/cpp-ubsan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-fsanitize=undefined -fno-omit-frame-pointer"
	cmake --build build/cpp-ubsan
	ctest --test-dir build/cpp-ubsan --output-on-failure

cpp-benchmark:
	cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release
	cmake --build build/cpp-release --config Release
	@mkdir -p benchmarks/results
	build/cpp-release/Release/fl_aggregation_benchmark.exe > benchmarks/results/aggregation_benchmark_latest.csv 2>&1 || \
	build/cpp-release/fl_aggregation_benchmark > benchmarks/results/aggregation_benchmark_latest.csv
