Scaled Dot-Product Attentio

Multi-Head Attenti

Figure 2: (left) Scaled Dot-Product Attention. (right) Multi-Head Attention consists of seven

of the values, where the weight assigned to each value is computed by a compatibility function of the

Thefooter><up>

We call our particular attention "Scaled Dot-Product Attention" (Figure 2). The input consists queries and keys of dimension $d_k$, and values of dimension $d_v$. We compute the dot products of the query with all keys, divide each by $\sqrt{d_k}$ ,and apply a softmax function to obtain the weights on the

values.
In practice, we compute the attention function on a set of queries simultaneously, packed together into a matrix $Q$. The keys and values are also packed together into matrices $K$ and $V$. We compute

"Lambda +ntian / $\bigcap$ `K` \nabla$ - $\text{coftman} / {QK^T}_{\nabla}$ "label:equation><up>

$ \mathcal{V}^{u_k} $ The two most commonly used attention functions are additive attention [2], and dot-product (multiplicative) attention. Dot-product attention is identical to our algorithm, except for the scaling fact of $ \frac{1}{\sqrt{d_k}} $. Additive attention computes the compatibility function using a feed-forward network with
a single hidden layer. While the two are similar in theoretical complexity, dot-product attention
much faster and more scarce-efficient in practice, since it can be implemented usin${\sigma }$ hishlv ontimized

matrix multiplication code.
While for small values of $d_k$ the two mechanisms perform similarly, additive attention outperform dot product attention without scaling for larger values of $d_k$ [3]. We suspect that for large values $d_k$, the dot products grow large in magnitude, pushing the softmax function into regions where it has

vavivny vumov 6000000000000000000000000000000000000000000000000000

3.2.2 Multi-Head Attention
Instead of performing a single attention function with $d_{\text{model-dimensional}}$ values, values and queries we found it beneficial to linearly project the queries, keys and values $h$ times with different, learning

queries, keys and values we then perform the attention function in parallel, yielding $d_v$-dimension $\left.\begin{array}{r} 4 \mathrm { T}_{\text{--}} \\ \end{array}\right.$

NULL