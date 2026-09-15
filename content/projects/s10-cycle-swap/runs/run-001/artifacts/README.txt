Exact exhaustive computation for the undirected Cayley graph on S_10.

The included computation.txt was produced by compiling diameter.cpp with:
  clang++ -std=c++17 -O3 diameter.cpp -o diameter
and executing:
  ./diameter

These commands document reproducibility; running them is not required to use the upload package.

Actual compiler: Apple clang version 21.0.0 (clang-2100.1.1.101).
Actual target: arm64-apple-darwin25.6.0.
Compilation and execution both returned exit code 0.
The executable and transient build files are deliberately omitted.

The optional integer argument selects n between 2 and 10. Only the default n=10 result is reported here. The full distance array is reconstructed by the program and is not included in the upload.

Checks performed: all n! vertices reached; every generator edge changes the distance label by at most one; every positive-distance vertex has a predecessor; the printed word evaluates to the printed permutation and has the asserted length.
