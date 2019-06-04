import torch
import math
from torch import nn

non_linearities = {
    "relu": nn.ReLU,
    "tanh": nn.Tanh,
}

class SparseLinear(torch.nn.Module):
    """
    Linear layer with sparse input tensor, and dense output.
        in_features    size of input
        out_features   size of output
        bias           whether to add bias
    """
    def __init__(self, in_features, out_features, bias=True):
        super(SparseLinear, self).__init__()
        self.weight = nn.Parameter(torch.randn(in_features, out_features) / math.sqrt(out_features))
        if bias:
            self.bias = nn.Parameter(torch.zeros(out_features))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / math.sqrt(self.weight.size(1))
        self.weight.data.uniform_(-stdv, stdv)
        if self.bias is not None:
            self.bias.data.zero_()

    def forward(self, input):
        out = torch.mm(input, self.weight)
        if self.bias is not None:
            return out + self.bias
        return out

    def extra_repr(self):
        return 'in_features={}, out_features={}, bias={}'.format(
            self.weight.shape[0], self.weight.shape[1], self.bias is not None
        )

class ModelConfig(object):
    def __init__(self,
        input_size,
        hidden_sizes,
        output_size,
        mapping            = None,
        hidden_dropout     = 0.0,
        last_dropout       = 0.0,
        weight_decay       = 0.0,
        input_size_freq    = None,
        tail_hidden_size   = None,
        non_linearity      = "relu",
        last_non_linearity = "relu",
    ):
        assert non_linearity in non_linearities.keys(), f"non_linearity can be either {non_linearities.keys()}."
        if mapping is not None:
            assert input_size == mapping.shape[0]

        self.mapping            = mapping
        self.input_size         = input_size
        self.hidden_sizes       = hidden_sizes
        self.output_size        = output_size
        self.hidden_dropout     = hidden_dropout
        self.last_dropout       = last_dropout
        self.last_non_linearity = last_non_linearity
        self.weight_decay       = weight_decay
        self.tail_hidden_size   = tail_hidden_size
        self.non_linearity      = non_linearity
        if input_size_freq is None:
            self.input_size_freq = input_size
        else:
            self.input_size_freq = input_size_freq
            assert self.input_size_freq <= input_size, f"Input size {input_size} is smaller than freq input size {self.input_size_freq}"

class SparseInputNet(torch.nn.Module):
    def __init__(self, conf):
        super().__init__()
        self.input_splits = [conf.input_size_freq, conf.input_size - conf.input_size_freq]
        self.net_freq   = SparseLinear(self.input_splits[0], conf.hidden_sizes[0])

        if self.input_splits[1] == 0:
            self.net_rare = None
        else:
            self.net_rare = nn.Sequential(
                SparseLinear(self.input_splits[1], self.tail_hidden_size),
                ## TODO: try if it is better
                #nn.ReLU(),
                ## Bias is not needed as net_freq provides it
                nn.Linear(self.tail_hidden_size, self.hidden_sizes[0], bias=False),
            )

    def forward(self, x_ind, x_data, num_rows):
        if self.input_splits[1] == 0:
            X = torch.sparse_coo_tensor(x_ind, x_data,
                    size=[num_rows, self.input_splits[0]])
            return self.net_freq(X)
        ## splitting into freq and rare
        mask_freq = x_ind[1] < self.input_splits[0]
        Xfreq = torch.sparse_coo_tensor(
                    indices = x_ind[:, mask_freq],
                    values  = x_data[mask_freq],
                    size    = [num_rows, self.input_splits[0]])
        Xrare = torch.sparse_coo_tensor(
                    indices = x_ind[:, ~mask_freq],
                    values  = x_data[~mask_freq],
                    size    = [num_rows, self.input_splits[1]])
        return self.net_freq(Xfreq) + self.net_rare(Xrare)

class IntermediateNet(torch.nn.Module):
    def __init__(self, conf):
        super().__init__()
        self.net = nn.Sequential()
        for i in range(len(conf.hidden_sizes) - 1):
            self.intermediate_net.add_module(nn.Sequential(
                nn.ReLU(),
                nn.Dropout(conf.hidden_dropout),
                nn.Linear(conf.hidden_sizes[i], conf.hidden_sizes[i+1]),
            ))
    def forward(self, H):
        return self.net(H)

class LastNet(torch.nn.Module):
    def __init__(self, conf):
        super().__init__()
        non_linearity = non_linearities[conf.last_non_linearity]
        self.net = nn.Sequential(
            non_linearity(),
            nn.Dropout(conf.last_dropout),
            nn.Linear(conf.hidden_sizes[-1], conf.output_size),
        )
    def forward(self, H):
        return self.net(H)

class SparseFFN(torch.nn.Module):
    def __init__(self, conf):
        super().__init__()

        self.input_net = SparseInputNet(conf)
        self.net = nn.Sequential(
            IntermediateNet(conf),
            LastNet(conf),
        )

    def forward(self, x_ind, x_data, num_rows):
        H = self.input_net(x_ind, x_data, num_rows)
        return self.net(H)


