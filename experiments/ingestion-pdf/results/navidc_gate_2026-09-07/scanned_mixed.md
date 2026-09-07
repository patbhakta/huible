Table 1: Maximum path lengths, per-layer complexity and minimum number of sequential operations for different layer types. $n$ is the sequence length,$d$ is the representation dimension,$k$ is the ker size of convolutions and $r$ the size of the neighborhood in restricted self-attention.

<fcel>Layer Type<fcel>Complexity per Layer<fcel>Sequential Operations<fcel>Maximum Path Length<nl><fcel>Self-Attention<fcel>$O(n^{2} \cdot d)$<fcel>O(1)<fcel>O(1)<nl><fcel>Recurrent<fcel>$O(n \cdot d^{2})$<fcel>O(n)<fcel>O(n)<nl><fcel>Convolutional<fcel>$O(k \cdot n \cdot d^{2})$<fcel>O(1)<fcel>$O(log_{k}(n))$<nl><fcel>Self-Attention (restricted)<fcel>$O(r \cdot n \cdot d)$<fcel>O(1)<fcel>$O(n/r)$<nl>

2 = Driftinall Finnandin

Since our model contains no recurrence and no convolution, in order for the model to make use of order of the sequence, we must inject some information about the relative or absolute position of tokens in the sequence. To this end, we add "positional encodings" to the input embeddings at bottoms of the encoder and decoder stacks. The positional encodings have the same dimension $d_{\mathrm{m}}$ as the embeddings, so that the two can be summed. There are many choices of positional encoding

The quick brown fox jumps over the lazy dog.

……ni/d

$\Gamma$ $L(pos,2i)=sin(\rho os/100\mathrm{UUU},...)$

where $pos$ is the position and $i$ is the dimension. That is, each dimension of the positional encode corresponds to a sinusoid. The wavelengths form a geometric progression from $2\pi$ to $10000 \cdot 2\pi$. chose this function because we hypothesized it would allow the model to easily learn to attend relative positions, since for any fixed offset $k$, $PE_{pos+k}$ can be represented as a linear function

$E^{\prime} E_{pos}$.
We also experimented with using learned positional embeddings [9] instead, and found that the  $t$ versions produced nearly identical results (see Table 3 row (E)). We chose the sinusoidal vers because it may allow the model to extrapolate to sequence lengths longer than the ones encountered

Thefooter><up>

In this section we compare various aspects of self-attention layers to the recurrent and conventional layers commonly used for mapping one variable-length sequence of symbol representation $ \left( {{x}_{1},\ldots ,{x}_{n}}\right) $ to another sequence of equal length $ \left({z}_{1},\ldots ,{z}_{n}\right) $, with $ {x}_{i},{z}_{i} \in {\mathbb{R}}^{d} $ , such as a hidden layer in a typical sequence transduction encoder or decoder Motivating our use of self-attention

consider three desiderata.
One is the total commutational convexity per layer Another is the amount of commutation that

be parallelized, as measured by the minimum number of sequential operations required.
The third is the path length between long-range dependencies in the network. Learning long-rar
dependencies is a key challenge in many sequence transduction tasks. One key factor affecting
ability to learn such dependencies is the length of the paths forward and backward signals have
traverse in the network. The shorter these paths between any combination of positions in the input
and output sequences, the easier it is to learn long-range dependencies [12]. Hence we also comp

different layer types.
As noted in Table 1, a self-attention layer connects all positions with a constant number of sequential

The quick brown fox jumps over the lazy dog.