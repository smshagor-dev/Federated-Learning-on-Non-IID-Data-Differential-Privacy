#include "fl_core/tensor.hpp"

#include <cmath>
#include <limits>

namespace fl::core {

namespace {

void ensure_compatible(const TensorBuffer& lhs, const TensorBuffer& rhs) {
    if (lhs.descriptor().name != rhs.descriptor().name) {
        throw std::invalid_argument("tensor names do not match");
    }
    if (lhs.descriptor().shape != rhs.descriptor().shape) {
        throw std::invalid_argument("tensor shapes do not match");
    }
}

TensorBuffer unary_apply(
    const TensorBuffer& tensor,
    const std::string& suffix,
    const std::function<double(double)>& fn
) {
    auto descriptor = tensor.descriptor();
    descriptor.name += suffix;
    std::vector<double> values;
    values.reserve(tensor.values().size());
    for (const auto value : tensor.values()) {
        values.push_back(fn(value));
    }
    descriptor.name = tensor.descriptor().name;
    return TensorBuffer(std::move(descriptor), std::move(values));
}

TensorBuffer binary_apply(
    const TensorBuffer& lhs,
    const TensorBuffer& rhs,
    const std::function<double(double, double)>& fn
) {
    ensure_compatible(lhs, rhs);
    auto descriptor = lhs.descriptor();
    std::vector<double> values;
    values.reserve(lhs.values().size());
    for (std::size_t index = 0; index < lhs.values().size(); ++index) {
        values.push_back(fn(lhs.values()[index], rhs.values()[index]));
    }
    return TensorBuffer(std::move(descriptor), std::move(values));
}

}  // namespace

std::size_t TensorDescriptor::element_count() const {
    std::size_t total = 1;
    for (const auto dimension : shape) {
        if (dimension == 0) {
            throw std::invalid_argument("tensor shape dimensions must be positive");
        }
        if (total > std::numeric_limits<std::size_t>::max() /
                static_cast<std::size_t>(dimension)) {
            throw std::invalid_argument("tensor shape element count overflows");
        }
        total *= static_cast<std::size_t>(dimension);
    }
    return total;
}

std::size_t TensorDescriptor::byte_length() const {
    if (element_count() > std::numeric_limits<std::size_t>::max() / sizeof(float)) {
        throw std::invalid_argument("tensor byte length overflows");
    }
    return element_count() * sizeof(float);
}

TensorBuffer::TensorBuffer(TensorDescriptor descriptor, std::vector<double> values)
    : descriptor_(std::move(descriptor)), values_(std::move(values)) {
    validate();
}

const TensorDescriptor& TensorBuffer::descriptor() const {
    return descriptor_;
}

const std::vector<double>& TensorBuffer::values() const {
    return values_;
}

std::vector<double>& TensorBuffer::values() {
    return values_;
}

bool TensorBuffer::empty() const {
    return values_.empty();
}

std::size_t TensorBuffer::size() const {
    return values_.size();
}

void TensorBuffer::validate() const {
    if (descriptor_.name.empty()) {
        throw std::invalid_argument("tensor name must not be empty");
    }
    if (descriptor_.shape.empty()) {
        throw std::invalid_argument("tensor shape must not be empty");
    }
    if (descriptor_.dtype != DType::kFloat32) {
        throw std::invalid_argument("unsupported tensor dtype");
    }
    if (descriptor_.element_count() != values_.size()) {
        throw std::invalid_argument("tensor element count does not match values size");
    }
    for (const auto value : values_) {
        if (!std::isfinite(value)) {
            throw std::invalid_argument("tensor contains non-finite value");
        }
    }
}

void TensorCollection::insert(TensorBuffer tensor) {
    tensor.validate();
    if (contains(tensor.descriptor().name)) {
        throw std::invalid_argument("duplicate tensor name");
    }
    tensors_[tensor.descriptor().name] = std::move(tensor);
}

void TensorCollection::assign(TensorBuffer tensor) {
    tensor.validate();
    tensors_[tensor.descriptor().name] = std::move(tensor);
}

bool TensorCollection::contains(const std::string& name) const {
    return tensors_.contains(name);
}

const TensorBuffer& TensorCollection::at(const std::string& name) const {
    return tensors_.at(name);
}

TensorBuffer& TensorCollection::at(const std::string& name) {
    return tensors_.at(name);
}

const std::map<std::string, TensorBuffer>& TensorCollection::tensors() const {
    return tensors_;
}

bool TensorCollection::empty() const {
    return tensors_.empty();
}

TensorBuffer zeros_like(const TensorDescriptor& descriptor) {
    return TensorBuffer(descriptor, std::vector<double>(descriptor.element_count(), 0.0));
}

TensorBuffer add(const TensorBuffer& lhs, const TensorBuffer& rhs) {
    return binary_apply(lhs, rhs, [](double left, double right) { return left + right; });
}

TensorBuffer subtract(const TensorBuffer& lhs, const TensorBuffer& rhs) {
    return binary_apply(lhs, rhs, [](double left, double right) { return left - right; });
}

TensorBuffer scale(const TensorBuffer& tensor, double factor) {
    auto descriptor = tensor.descriptor();
    std::vector<double> values;
    values.reserve(tensor.values().size());
    for (const auto value : tensor.values()) {
        values.push_back(value * factor);
    }
    return TensorBuffer(std::move(descriptor), std::move(values));
}

TensorBuffer divide(const TensorBuffer& tensor, double divisor) {
    if (divisor == 0.0) {
        throw std::invalid_argument("division by zero");
    }
    return scale(tensor, 1.0 / divisor);
}

TensorBuffer hadamard_square(const TensorBuffer& tensor) {
    return unary_apply(tensor, "", [](double value) { return value * value; });
}

TensorBuffer hadamard_sqrt(const TensorBuffer& tensor) {
    return unary_apply(tensor, "", [](double value) { return std::sqrt(value); });
}

TensorBuffer hadamard_abs(const TensorBuffer& tensor) {
    return unary_apply(tensor, "", [](double value) { return std::abs(value); });
}

TensorBuffer hadamard_sign(const TensorBuffer& tensor) {
    return unary_apply(
        tensor,
        "",
        [](double value) { return (value > 0.0) - (value < 0.0); }
    );
}

TensorBuffer add_scalar(const TensorBuffer& tensor, double value) {
    auto descriptor = tensor.descriptor();
    std::vector<double> values;
    values.reserve(tensor.values().size());
    for (const auto current : tensor.values()) {
        values.push_back(current + value);
    }
    return TensorBuffer(std::move(descriptor), std::move(values));
}

TensorBuffer divide_elementwise(const TensorBuffer& lhs, const TensorBuffer& rhs) {
    return binary_apply(lhs, rhs, [](double left, double right) {
        if (right == 0.0) {
            throw std::invalid_argument("elementwise division by zero");
        }
        return left / right;
    });
}

}  // namespace fl::core
