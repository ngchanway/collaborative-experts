[**Notice**]: **We have recently become aware of a bug in our codebase that affects the results reported both on this page and in the paper.** The bug effectively leaks information to the retrieval system at test time that it should not have access to. We are working to update the code and will report corrected results as soon as possible. However, in the meantime:
1. Until we have fixed and fully tested the code, we recommend that you don't base your work on our implementation.
2. If you are submitting to upcoming deadlines and were planning to evaluate your method on the benchmarks we report numbers on, please point the reviewer to the [project page](https://www.robots.ox.ac.uk/~vgg/research/collaborative-experts/) so that you are not penalised for avoiding a comparison with our inaccurate numbers.

We have removed the links to the models until we have resolved the bug and corrected the experiments (if you would still like to find the tables and links from before, they can be found by rolling back a couple of commits).

We would like to thank Valentin Gabeur who spotted and notified us of the issue.

---


This repo provides code for learning and evaluating joint video-text embeddings for the task of video retrieval.  Our approach is described in the BMVC 2019 paper "Use What You Have: Video retrieval using representations from collaborative experts" ([paper](https://arxiv.org/abs/1907.13487), [project page](https://www.robots.ox.ac.uk/~vgg/research/collaborative-experts/)).



![CE diagram](figs/CE.png)



**High-level Overview**: The *Collaborative Experts* framework aims to achieve robustness through two mechanisms:
1. The use of information from a wide range of modalities, including those that are typically always available in video (such as RGB) as well as more "specific" clues which may only occasionally be present (such as overlaid text).
2. A module that aims to combine these modalities into a fixed size representation that in a manner that is robust to noise.
