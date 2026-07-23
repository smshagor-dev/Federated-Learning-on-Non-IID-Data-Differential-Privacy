#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fl::core {

enum class DType {
    kFloat32,
};

struct TensorDescriptor {
    std::string name;
    std::vector<std::uint64_t> shape;
    DType dtype{DType::kFloat32};

    [[nodiscard]] std::size_t element_count() const;
    [[nodiscard]] std::size_t byte_length() const;
};

class TensorBuffer {
public:
    TensorBuffer() = default;
    TensorBuffer(TensorDescriptor descriptor, std::vector<double> values);

    [[nodiscard]] const TensorDescriptor& descriptor() const;
    [[nodiscard]] const std::vector<double>& values() const;
    [[nodiscard]] std::vector<double>& values();
    [[nodiscard]] bool empty() const;
    [[nodiscard]] std::size_t size() const;

    void validate() const;

private:
    TensorDescriptor descriptor_;
    std::vector<double> values_;
};

class TensorCollection {
public:
    void insert(TensorBuffer tensor);
    void assign(TensorBuffer tensor);
    [[nodiscard]] bool contains(const std::string& name) const;
    [[nodiscard]] const TensorBuffer& at(const std::string& name) const;
    [[nodiscard]] TensorBuffer& at(const std::string& name);
    [[nodiscard]] const std::map<std::string, TensorBuffer>& tensors() const;
    [[nodiscard]] bool empty() const;

private:
    std::map<std::string, TensorBuffer> tensors_;
};

TensorBuffer zeros_like(const TensorDescriptor& descriptor);
TensorBuffer add(const TensorBuffer& lhs, const TensorBuffer& rhs);
TensorBuffer subtract(const TensorBuffer& lhs, const TensorBuffer& rhs);
TensorBuffer scale(const TensorBuffer& tensor, double factor);
TensorBuffer divide(const TensorBuffer& tensor, double divisor);
TensorBuffer hadamard_square(const TensorBuffer& tensor);
TensorBuffer hadamard_sqrt(const TensorBuffer& tensor);
TensorBuffer hadamard_abs(const TensorBuffer& tensor);
TensorBuffer hadamard_sign(const TensorBuffer& tensor);
TensorBuffer add_scalar(const TensorBuffer& tensor, double value);
TensorBuffer divide_elementwise(const TensorBuffer& lhs, const TensorBuffer& rhs);

}  // namespace fl::core
