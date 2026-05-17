#include <algorithm>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <string>
#include <vector>

namespace py = pybind11;

namespace fuzzy {
// UTF-8 ДЕКОДЕР
inline std::u32string utf8_to_utf32(const std::string &str) {
  std::u32string result;
  size_t i = 0;
  while (i < str.size()) {
    unsigned char c = str[i];
    if (c <= 0x7F) {
      result.push_back(c);
      i += 1;
    } else if ((c & 0xE0) == 0xC0) {
      if (i + 1 < str.size()) {
        result.push_back(((c & 0x1F) << 6) | (str[i + 1] & 0x3F));
      }
      i += 2;
    } else if ((c & 0xF0) == 0xE0) {
      if (i + 2 < str.size()) {
        result.push_back(((c & 0x0F) << 12) | ((str[i + 1] & 0x3F) << 6) |
                         (str[i + 2] & 0x3F));
      }
      i += 3;
    } else if ((c & 0xF8) == 0xF0) {
      if (i + 3 < str.size()) {
        result.push_back(((c & 0x07) << 18) | ((str[i + 1] & 0x3F) << 12) |
                         ((str[i + 2] & 0x3F) << 6) | (str[i + 3] & 0x3F));
      }
      i += 4;
    } else {
      i += 1;
    }
  }
  return result;
}

inline int distance_core(const std::u32string &a, const std::u32string &b) {
  const std::u32string &s1 = a.size() < b.size() ? a : b;
  const std::u32string &s2 = a.size() < b.size() ? b : a;

  std::vector<int> prev(s1.size() + 1), curr(s1.size() + 1);

  for (size_t i = 0; i <= s1.size(); i++)
    prev[i] = i;

  for (size_t j = 1; j <= s2.size(); j++) {
    curr[0] = j;
    for (size_t i = 1; i <= s1.size(); i++) {
      if (s1[i - 1] == s2[j - 1])
        curr[i] = prev[i - 1];
      else
        curr[i] = 1 + std::min({prev[i], curr[i - 1], prev[i - 1]});
    }
    std::swap(prev, curr);
  }
  return prev[s1.size()];
}

inline double similarity(const std::string &a_str, const std::string &b_str) {
  std::u32string a = utf8_to_utf32(a_str);
  std::u32string b = utf8_to_utf32(b_str);

  int max_len = std::max(a.size(), b.size());
  if (max_len == 0)
    return 1.0;

  int dist = distance_core(a, b);
  return 1.0 - (double)dist / max_len;
}

std::string find_best_match(const std::string &target,
                            const std::vector<std::string> &choices,
                            double threshold = 0.8) {
  std::string best_match = "";
  double highest_sim = 0.0;

  for (const auto &choice : choices) {
    double sim = similarity(target, choice);
    if (sim >= threshold && sim > highest_sim) {
      highest_sim = sim;
      best_match = choice;
    }
  }
  return best_match;
}
} // namespace fuzzy

PYBIND11_MODULE(fuzzy_ext, m) {
  m.doc() =
      "Швидкий модуль нечіткого пошуку на C++ з підтримкою UTF-8 (Кирилиці)";

  m.def("similarity", &fuzzy::similarity,
        "Calculate similarity between two strings (0.0 to 1.0)", py::arg("a"),
        py::arg("b"));

  m.def("find_best_match", &fuzzy::find_best_match,
        "Find best fuzzy match from a list of strings", py::arg("target"),
        py::arg("choices"), py::arg("threshold") = 0.8);
}
