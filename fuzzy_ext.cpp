#include <pybind11/pybind11.h>
#include <pybind11/stl.h> // Конвертує Python list у std::vector
#include <string>
#include <vector>
#include <algorithm>

namespace py = pybind11;

namespace fuzzy {
    inline int distance(const std::string& a, const std::string& b) {
        const std::string& s1 = a.size() < b.size() ? a : b;
        const std::string& s2 = a.size() < b.size() ? b : a;

        std::vector<int> prev(s1.size() + 1), curr(s1.size() + 1);

        for (size_t i = 0; i <= s1.size(); i++) prev[i] = i;

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

    inline double similarity(const std::string& a, const std::string& b) {
        int max_len = std::max(a.size(), b.size());
        if (max_len == 0) return 1.0;
        int dist = distance(a, b);
        return 1.0 - (double)dist / max_len;
    }

    std::string find_best_match(const std::string& target, const std::vector<std::string>& choices, double threshold = 0.8) {
        std::string best_match = "";
        double highest_sim = 0.0;

        for (const auto& choice : choices) {
            double sim = similarity(target, choice);
            if (sim >= threshold && sim > highest_sim) {
                highest_sim = sim;
                best_match = choice;
            }
        }
        return best_match; 
    }
}

PYBIND11_MODULE(fuzzy_ext, m) {
    m.doc() = "Швидкий модуль нечіткого пошуку на C++";
    
    m.def("similarity", &fuzzy::similarity, 
          "Calculate similarity between two strings (0.0 to 1.0)",
          py::arg("a"), py::arg("b"));

    m.def("find_best_match", &fuzzy::find_best_match, 
          "Find best fuzzy match from a list of strings",
          py::arg("target"), py::arg("choices"), py::arg("threshold") = 0.8);
}