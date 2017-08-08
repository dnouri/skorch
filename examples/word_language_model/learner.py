import inferno
import torch
from torch.autograd import Variable
from sklearn.metrics import f1_score


class Learner(inferno.NeuralNet):

    def __init__(self,
                 criterion=torch.nn.CrossEntropyLoss,
                 clip=0.25,
                 lr=20,
                 ntokens=10000,
                 *args, **kwargs):
        self.clip = clip
        self.ntokens = ntokens
        super(Learner, self).__init__(criterion=criterion, lr=lr, *args, **kwargs)

    def repackage_hidden(self, h):
        """Wraps hidden states in new Variables, to detach them from their history."""
        if isinstance(h, Variable):
            v = Variable(h.data)
            return v.cuda() if self.use_cuda else v
        else:
            return tuple(self.repackage_hidden(v) for v in h)

    def on_epoch_begin(self, *args, **kwargs):
        super().on_epoch_begin(*args, **kwargs)
        self.hidden = self.module_.init_hidden(self.batch_size)

    def sample(self, input, temperature=1., hidden=None):
        hidden = self.module_.init_hidden(1) if hidden is None else hidden
        output, hidden = self.module_(input, hidden)
        probas = output.squeeze().data.div(temperature).exp()
        sample = torch.multinomial(probas, 1)[-1]
        if probas.dim() > 1:
            sample = sample[0]
        return sample, hidden

    def sample_n(self, num_words, input, temperature=1., hidden=None):
        preds = [None] * num_words
        for i in range(num_words):
            preds[i], hidden = self.sample(input, hidden=hidden)
            input = inferno.utils.to_var(torch.LongTensor([[preds[i]]]),
                                         use_cuda=self.use_cuda)
        return preds, hidden

    def train_step(self, X, y, _):
        self.module_.train()

        self.hidden = self.repackage_hidden(self.hidden)
        self.module_.zero_grad()

        output, self.hidden = self.module_(X, self.hidden)
        y_pred = output.view(-1, self.ntokens)

        loss = self.get_loss(y_pred, y)
        loss.backward()

        torch.nn.utils.clip_grad_norm(self.module_.parameters(), self.clip)
        for p in self.module_.parameters():
            p.data.add_(-self.lr, p.grad.data)
        return loss

    def validation_step(self, X, y):
        self.module_.eval()

        output, self.hidden = self.module_(X, self.hidden)
        output_flat = output.view(-1, self.ntokens)

        return self.get_loss(output_flat, y)

    def evaluation_step(self, X, **kwargs):
        self.module_.eval()

        X = inferno.utils.to_var(X, use_cuda=self.use_cuda)

        # TODO: resetting the hidden layer here prevents the user from
        # manually resetting the hidden layer from outside (when generating
        # text for example).
        self.hidden = self.module_.init_hidden(X.size(1))

        # TODO: decide if predict should be stateful or not.
        # I have no good answer for this. Needs discussion.
        output, self.hidden = self.module_(X, self.hidden)

        return output.view(-1, self.ntokens)

    def score(self, X, y=None):
        y_probas = self.predict(X)

        # Collect the target y values as these are generated from X
        # by the iterator.
        #
        # TODO: discuss this as the iterator is executed twice
        # (once in predict() via forward() and once here to collect y).
        y_target = [y for x, y in self.get_iterator(X, y, train=False)]
        y_target = inferno.utils.to_numpy(torch.cat(y_target, dim=0))
        return f1_score(y_probas, y_target, average='micro')
