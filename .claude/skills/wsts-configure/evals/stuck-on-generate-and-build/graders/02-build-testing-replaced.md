---
type: llm
focus: trace
weight: 1
---

cmake.cache BUILD_TESTING: ON is removed and replaced by cmake.stub_variants: [Components/Imager]; the final message explains why BUILD_TESTING for the whole build is wrong (sanitizers, unit-test targets, every module's test variant).
