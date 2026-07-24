#pragma once

#include <functional>
#include <iostream>
#include <string>

namespace fl::coordinator::testing {

inline int g_failures = 0;

inline void check(bool condition, const std::string& label) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++g_failures;
    }
}

inline void expect_throw(const std::function<void()>& action, const std::string& label) {
    bool threw = false;
    try {
        action();
    } catch (const std::exception&) {
        threw = true;
    }
    check(threw, label + " (expected an exception, none was thrown)");
}

inline void expect_no_throw(const std::function<void()>& action, const std::string& label) {
    try {
        action();
    } catch (const std::exception& error) {
        check(false, label + " (unexpected exception: " + error.what() + ")");
    }
}

}  // namespace fl::coordinator::testing
