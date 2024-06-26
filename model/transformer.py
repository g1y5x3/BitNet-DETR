import copy
import torch
import torch.nn.functional as F
from torch import nn, Tensor


class MultiheadAttention(nn.Module):
  def __init__(self, embed_dim: int, num_heads: int, dropout: float=0.0, bias: bool=True, linear_layer: nn.Module=nn.Linear):
    super().__init__()
    self.embed_dim = embed_dim
    self.num_heads = num_heads
    self.head_dim = embed_dim // num_heads
    self.dropout = dropout

    self.q_proj = linear_layer(embed_dim, embed_dim, bias=bias)
    self.k_proj = linear_layer(embed_dim, embed_dim, bias=bias)
    self.v_proj = linear_layer(embed_dim, embed_dim, bias=bias)
    self.out_proj = linear_layer(embed_dim, embed_dim, bias=bias)

  def forward(self, query: Tensor, key: Tensor, value: Tensor, key_padding_mask: Tensor=None):
    bsz, tgt_len, _ = query.size()
    src_len = key.size(1)

    q = self.q_proj(query)
    k = self.k_proj(key)
    v = self.v_proj(value)

    q = q.view(bsz, tgt_len, self.num_heads, self.head_dim).transpose(1,2)
    k = k.view(bsz, src_len, self.num_heads, self.head_dim).transpose(1,2)
    v = v.view(bsz, src_len, self.num_heads, self.head_dim).transpose(1,2)

    attn_mask = key_padding_mask.view(bsz, 1, 1, src_len).expand(-1, self.num_heads, -1, -1) if key_padding_mask is not None else None
    dropout_p = 0.0 if not self.training else self.dropout 

    # NOTE: currently torch doesn't support flash attention with attn_mask
    attn_output = F.scaled_dot_product_attention(q, k, v, attn_mask, dropout_p=dropout_p)

    attn_output = attn_output.transpose(1,2).view(bsz, tgt_len, self.num_heads*self.head_dim)
    attn_output = self.out_proj(attn_output)

    return attn_output

class TransformerEncoder(nn.Module):
  def __init__(self, encoder_layer: nn.Module, num_layers: int):
    super().__init__()
    self.num_layers = num_layers
    self.layers = nn.ModuleList([copy.deepcopy(encoder_layer) for _ in range(num_layers)])

  def forward(self, src: Tensor, src_key_padding_mask: Tensor, src_pos: Tensor):
    output = src
    for layer in self.layers: 
      output = layer(output, src_key_padding_mask, src_pos)

    return output

class TransformerDecoder(nn.Module):
  def __init__(self, decoder_layer: nn.Module, num_layers: int):
    super().__init__()
    self.num_layers = num_layers
    self.layers = nn.ModuleList([copy.deepcopy(decoder_layer) for _ in range(num_layers)])

  def forward(self, memory: Tensor, memory_key_padding_mask: Tensor, memory_pos: Tensor, tgt: Tensor, query_pos: Tensor):
    output = tgt
    for layer in self.layers:
      output = layer(memory, memory_key_padding_mask, memory_pos, output, query_pos)
    
    return output

class DETREncoderLayer(nn.Module):
  def __init__(self, d_model: int, nhead: int, dim_feedforward: int=2048, dropout: float=0.1, linear_layer: nn.Module=nn.Linear):
    super().__init__()
    self.self_attn = MultiheadAttention(d_model, nhead, dropout=dropout, linear_layer=linear_layer)

    self.linear1 = linear_layer(d_model, dim_feedforward)
    self.dropout = nn.Dropout(dropout) 
    self.linear2 = linear_layer(dim_feedforward, d_model)

    self.norm1 = nn.LayerNorm(d_model)
    self.norm2 = nn.LayerNorm(d_model)
    self.dropout1 = nn.Dropout(dropout)
    self.dropout2 = nn.Dropout(dropout)

    self.activation = nn.ReLU()

  def forward(self, src: Tensor, src_key_padding_mask: Tensor, src_pos: Tensor):
    # self-attention
    q = k = src + src_pos
    self_attn = self.self_attn(q, k, src, key_padding_mask=src_key_padding_mask)
    src = src + self.dropout1(self_attn)
    src = self.norm1(src)

    # feedforward
    ff = self.linear2(self.dropout(self.activation(self.linear1(src))))
    src = src + self.dropout2(ff)
    src = self.norm2(src)

    return src
  
class ViTEncoderLayer(nn.Module):
  def __init__(self, d_model: int, nhead: int, dim_feedforward: int=2048, dropout: float=0.1, linear_layer: nn.Module=nn.Linear):
    super().__init__()
    self.self_attn = MultiheadAttention(d_model, nhead, dropout=dropout, linear_layer=linear_layer)

    self.linear1 = linear_layer(d_model, dim_feedforward)
    self.dropout = nn.Dropout(dropout) 
    self.linear2 = linear_layer(dim_feedforward, d_model)

    self.norm1 = nn.LayerNorm(d_model)
    self.norm2 = nn.LayerNorm(d_model)
    self.dropout1 = nn.Dropout(dropout)
    self.dropout2 = nn.Dropout(dropout)

    self.activation = nn.GELU()

  def forward(self, src: Tensor, src_key_padding_mask: Tensor = None):
    # self-attention
    src_norm = self.norm1(src)
    self_attn = self.self_attn(src_norm, src_norm, src_norm, key_padding_mask=src_key_padding_mask)
    src = src + self.dropout1(self_attn)

    # feedforward
    src_norm = self.norm2(src)
    ff = self.linear2(self.dropout(self.activation(self.linear1(src_norm))))
    src = src + self.dropout2(ff)

    return src

class DETRDecoderLayer(nn.Module):
  def __init__(self, d_model: int, nhead: int, dim_feedforward: int=2048, dropout: float=0.1, linear_layer: nn.Module=nn.Linear):
    super().__init__()
    self.self_attn  = MultiheadAttention(d_model, nhead, dropout=dropout, linear_layer=linear_layer)
    self.cross_attn = MultiheadAttention(d_model, nhead, dropout=dropout, linear_layer=linear_layer)

    self.linear1 = linear_layer(d_model, dim_feedforward)
    self.dropout = nn.Dropout(dropout) 
    self.linear2 = linear_layer(dim_feedforward, d_model)

    self.norm1 = nn.LayerNorm(d_model)
    self.norm2 = nn.LayerNorm(d_model)
    self.norm3 = nn.LayerNorm(d_model)
    self.dropout1 = nn.Dropout(dropout)
    self.dropout2 = nn.Dropout(dropout)
    self.dropout3 = nn.Dropout(dropout)

    self.activation = nn.ReLU()

  def forward(self, memory: Tensor, memory_key_padding_mask: Tensor, memory_pos: Tensor,
                    tgt: Tensor, query_pos: Tensor):
    # self-attention
    q = k = tgt + query_pos # positional embedding is also added to each layer of the decoder
    self_attn = self.self_attn(q, k, tgt)
    tgt = tgt + self.dropout1(self_attn)
    tgt = self.norm1(tgt)

    # cross-attention
    q = tgt + query_pos
    k = memory + memory_pos
    cross_attn = self.cross_attn(q, k, memory, key_padding_mask=memory_key_padding_mask)
    tgt = tgt + self.dropout2(cross_attn)
    tgt = self.norm2(tgt)

    # feedforward
    ff = self.linear2(self.dropout(self.activation(self.linear1(tgt))))
    tgt = tgt + self.dropout3(ff)
    tgt = self.norm3(tgt)

    return tgt

class DETRTransformer(nn.Module):
  def __init__(self, d_model=512, nhead=8, num_encoder_layers=6, num_decoder_layers=6, dim_feedforward=2048, dropout=0.1,
               linear_layer: nn.Module=nn.Linear):
    super().__init__()
    self.d_model = d_model
    self.nhead   = nhead

    encoder_layer = DETREncoderLayer(d_model, nhead, dim_feedforward, dropout, linear_layer)
    self.encoder  = TransformerEncoder(encoder_layer, num_encoder_layers)

    decoder_layer = DETRDecoderLayer(d_model, nhead, dim_feedforward, dropout, linear_layer)
    self.decoder  = TransformerDecoder(decoder_layer, num_decoder_layers)

  # assume that the input is already flattened
  def forward(self, src: Tensor, src_padding_mask: Tensor, src_pos: Tensor, query_pos: Tensor):
    memory = self.encoder(src=src, src_key_padding_mask=src_padding_mask, src_pos=src_pos)
  
    tgt = torch.zeros_like(query_pos)
    output = self.decoder(memory=memory, memory_key_padding_mask=src_padding_mask, memory_pos=src_pos, tgt=tgt, query_pos=query_pos)
  
    return output