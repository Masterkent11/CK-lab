#include <gtest/gtest.h>

#include <array>
#include <span>

TEST(Toolchain, SupportsCpp20Span) {
  constexpr std::array values{1, 2, 3};
  const std::span<const int> view{values};

  EXPECT_EQ(view.size(), 3u);
  EXPECT_EQ(view.back(), 3);
}
