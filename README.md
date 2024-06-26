# BitLinear-Vision-Transformers
Since the paper [The Era of 1-bit LLMs](https://arxiv.org/pdf/2402.17764v1.pdf) was relased, it makes me wonder whether training transformers with
the proposed `BitLinear` can also work across all modality on applications other than LLMs, for example, vision based models such as
~~ViT~~([TerViT](https://arxiv.org/abs/2201.08050) but no source code that I can find), DETR, DINO, LlaVa etc.

## DETR (Detection Transformer)
After some attempts to modify DETR base on some of the most popular computer vision libraries such as __ultralytics__, __mmdet__, __detectron2__, it
felt like I was editing _yaml_ files most of the time which was quite frustrating. The implementation from __huggingface__ seems more straight forward
but I got lost in too many if statements and hard for me to understand what the original DETR model is about. Therefore I based this repo off the 
[original detr repo](https://github.com/facebookresearch/detr), which was a bit out dated, e.g, it didn't support mixed precision, it has low GPU utilization 
during training. Therefore, I decided to rewrite everything from scratchwith the goal to make it easy to read, study, and hack around.
__(still a work in progress to remove the complexity, dataloading and preprocessing is another big mess)__

## Notes on BitLinear
### Formulation
$y = f(x) = \tilde{W}\tilde{x}$
 - The tenarization of a weight $W \in \mathbb{R}^{n \times m}$ can be formulated as:

   $\tilde{W} = {RoundClip}(\dfrac{W}{\beta+\epsilon}, -1, 1)$
    
   where $RoundClip(x, a, b)=max(a, min(b, round(x)))$, and $\displaystyle \beta = \frac{1}{nm}\sum_{ij}|W_{ij}|$.

 - The activations are further quantized to $b$-bit precision by using absmax quantization, which scales activations into the range
   $[-Q_b, Q_b] (Q_b=2^b-1)$ by multiplying with $Q_b$ and dividing by the absolute maximum of the input matrix:
 
   $\tilde{x} = Quant(x) = Clip(\dfrac{xQ_b}{\gamma}, -Q_b+\epsilon, Q_b-\epsilon)$
   
   where $Clip(x, a, b)=max(a, min(b, x))$, and $\gamma = ||x||_{\infty}$

### Implementation
1. Based on the implementations provided by 
   [FAQ](https://github.com/microsoft/unilm/blob/master/bitnet/The-Era-of-1-bit-LLMs__Training_Tips_Code_FAQ.pdf),
   both `x` and `w` are still in `float16` during training. However, they do get __quantized__ to maintain the property of 8 bits for `x` and ternary
   for `w`. Both `x_quant` and `w_quant` are also __rescaled__ before `F.linear` which becomes

   $f(x)=(\beta\tilde{W})(\dfrac{\gamma\tilde{x}}{Q_b})$

   ```python
   x_scale = 127.0 / x.abs().max(dim=-1, keepdim=True).values.clamp(min=1e-5)
   x_quant = (x * x_scale).round().clamp(-128, 127)
   x_quant = x + (x_quant / x_scale - x).detach()
   
   w_scale = 1.0 / w.abs().mean().clamp(min=1e-5)
   w_quant = (w * w_scale).round().clamp(-1, 1)
   w_quant = w + (w_quant / w_scale - w).detach()
   
   output  = F.linear(x_quant, w_quant)
   ```
   Using `.detach()` is a trick to employ straight-through estimator to make `F.linear` think it is still calculating 
   $f(x)=Wx$ instead of $\tilde{W}\tilde{x}$, which can bypass the non-differentiable functions such as $RoundClip$ and $Clip$. The resulting gradient 
   then becomes $\nabla f = \dfrac{\partial f}{\partial W} = x$.

   The [FAQ](https://github.com/microsoft/unilm/blob/master/bitnet/The-Era-of-1-bit-LLMs__Training_Tips_Code_FAQ.pdf) also mentioned,
   > the standard *F.linear* operation is replaced with a customized low-bit kernel.
   
   > With FP8 GEMM kernels, we can use FP8 activations for training and quantize to INT8 for inference on GPU devices with CUDA comptue capability < 9.0.
   
   source code of the custom kernels can be found in [BitBLAS](https://github.com/microsoft/BitBLAS).

2. this operation mathmatically is equivalent to 
   $f(x)=(\beta\tilde{W})(\dfrac{\gamma\tilde{x}}{Q_b})=\tilde{W}\tilde{x}(\dfrac{\beta\gamma}{Q_b})$.
   which means both scaling factors can be applied to the output of `F.linear` instead of its inputs.

   ```python
   x_scale = 127.0 / x.abs().max(dim=-1, keepdim=True).values.clamp(min=1e-5)
   x_quant = (x * x_scale).round().clamp(-128, 127)
   x_quant = x + (x_quant - x).detach()
   
   w_scale = 1.0 / w.abs().mean().clamp(min=1e-5)
   w_quant = (w * w_scale).round().clamp(-1, 1)
   w_quant = w + (w_quant - w).detach()
   
   output  = F.linear(x_quant, w_quant) / (x_scale * w_scale)
   ```
   `x_quant` and `w_quant` are $[-127, 127]$ (INT8) and $\{-1, 0, 1\}$ (~INT2).

3. If allowing $x$ to stay at FP16 but only quantize and rescale $W$ to tenary, it essentially becomes $f(x)=\tilde{W}\tilde{x}\beta$
   ```python
   output  = F.linear(x, w_quant) / w_scale
   ```

Due to floating-point arithmetic not always being associative or commutative, the outputs slightly diverge even though they are mathmatically 
equivalent. A few tests in [models/bitlinear.py](models/bitlinear.py#L60) were created to demonstrate this.

## Reults
### DETR
To clarify, the ResNet-50 backbone are still in FP16 since there is no ternary weight backbone available at this point. During training, all the
weights from the transformer were completely ternarized which resulted at around 17M ternarized parameters out of 40M total model parameters.

__Currently, the analysis are based on training the model for only 1 epoch. Perform a full training on the COCO dataset would take days given the 
compute resouces that is avaiable to me.__ As you can see in the loss curve, when fully quantize the inputs into 8 bits and the weights into ternary 
state, the model's training loss suffers pretty significantly. However, if keeping the inputs at FP16 precision and only ternaried the model weights,
the gap between the quantized model and the original model becomes much closer.
<figure>
  <img src="figures/train_detr_1epoch.png">
  <figcaption>Comparison between using nn.Linear (fp16 X fp16) and BitLinear (fp16 X int1.58-simulated, int8-simulated X int1.58-simulated) in the transformer of DETR.</figcaption>
</figure>

## TODO
- [x] rewrite the model to make the coder simplier, more readable, and easy to study.
   - [x] implement `MultiheadAttention` from scratch but keep `F.scaled_dot_product_attention` to utilized the optimized flash attentions kernel.
   - [x] remove the entirety of `NestedTensor` in DETR, the forward pass now takes two arguments both padded img and padding mask 
   - [x] simply `SetCriterion` which is the biggest bottleneck of the training (need to profile it), only `l1_loss`, `giou_loss`, and `cross_entropy`
         were used to compute the gradients. Additionally, using `torch.Tensor` instead of a dictionary so the `all_reduce` can be applied 
         automatically. 
   - [x] training in float16 using `amp`
   - [x] deepspeed integration for multigpu training 
- [ ] Use custom kernels from [BitBLAS](https://github.com/microsoft/BitBLAS/tree/main) for `F.linear`, however currrently it doesn't support autograd.
      In addition, based on their [reported benchmarks](https://github.com/microsoft/BitBLAS/blob/main/images/figures/op_benchmark_a100_wq_gemm_e7.png), 
      you only gain significant speed improvement when computing the GEMM in INT8xINT2.
- [ ] Train a ViT, SwinViT backbone with ternaried weights. Specifically, swin-v2 has a 3B parameters model which would put it at the same parameter 
      scale with the model size reported in the [BitNet1.58 paper](https://arxiv.org/pdf/2402.17764)
- [ ] Once there's a backbone with ternarized weight, perform a full COCO training comparison
- [ ] Rewrite the image preprocessing from scratch utilizing `Albumentation`, this is surprisingly painful right now. Maybe even benchmark it against 
      `torchvision.transform.v2`
- [ ] Try `BitLinear` on DINO, LlaVa.
