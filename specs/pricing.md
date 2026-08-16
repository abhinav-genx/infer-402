# Pricing rules

All prices are decimal strings denominated in USD per one million tokens. Cached input, uncached
input, and output are priced separately. The percentage markup is represented in basis points, then
the fixed fee is added. Convert the result to six-decimal USDC and round upward to the nearest
atomic unit. Finally, cap the result at the signed maximum.

Never use IEEE-754 floating-point values for payment calculations.
