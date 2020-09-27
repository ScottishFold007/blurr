# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/02a_modeling-token-classification.ipynb (unless otherwise specified).

__all__ = ['calculate_token_class_metrics', 'HF_TokenClassCallback']

# Cell
import ast, torch
from transformers import *
from fastai.text.all import *

from ..data.all import *
from .core import *

from seqeval import metrics as seq_metrics

# Cell
def calculate_token_class_metrics(pred_toks, targ_toks, metric_key):
    if (metric_key == 'accuracy'): return seq_metrics.accuracy_score(targ_toks, pred_toks)
    if (metric_key == 'precision'): return seq_metrics.precision_score(targ_toks, pred_toks)
    if (metric_key == 'recall'): return seq_metrics.recall_score(targ_toks, pred_toks)
    if (metric_key == 'f1'): return seq_metrics.f1_score(targ_toks, pred_toks)

    if (metric_key == 'classification_report'): return seq_metrics.classification_report(targ_toks, pred_toks)


# Cell
class HF_TokenClassCallback(HF_BaseModelCallback):
    """A fastai friendly callback that includes accuracy, precision, recall, and f1 metrics using the
    `seqeval` library.  Additionally, this metric knows how to *not* include your 'ignore_token' in it's
    calculations.

    See [here](https://github.com/chakki-works/seqeval) for more information on `seqeval`.
    """
    def __init__(self, tok_metrics=["accuracy", "precision", "recall", "f1"], **kwargs):
        self.run_before = Recorder

        store_attr(self=self, names='tok_metrics, kwargs')
        self.custom_metrics_dict = { k:None for k in tok_metrics }

        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if (not self.do_setup): return

        # grab the hf_tokenizer from the target's HF_TokenizerTransform (used for rouge metrics)
        hf_textblock_tfm = self.dls.before_batch[0]
        self.hf_tokenizer = hf_textblock_tfm.hf_tokenizer
        self.ignore_label_token_id = self.dls.tfms[1].ignore_token_id
        self.tok_special_symbols = list(self.hf_tokenizer.special_tokens_map.values())
        self.tok_kwargs = hf_textblock_tfm.kwargs

        # add custom text generation specific metrics
        custom_metric_keys = self.custom_metrics_dict.keys()
        custom_metrics = L([ ValueMetric(partial(self.metric_value, metric_key=k), k) for k in custom_metric_keys ])
        self.learn.metrics = self.learn.metrics + custom_metrics
        self.learn.token_classification_report = None

        self.do_setup = False

    def before_fit(self): self.setup()


    # --- batch begin/after phases ---
    def after_batch(self):
        if (self.training or self.learn.y is None): return

        # do this only for validation set
        preds = self.pred.argmax(dim=-1)
        targs = self.yb[0] # yb is TensorText tuple, item 0 is the data

        preds_list, targets_list = [], []
        for i in range(targs.shape[0]):
            item_targs, item_preds = [], []

            for j in range(targs.shape[1]):
                if (targs[i, j] != self.ignore_label_token_id):
                    item_preds.append(self.dls.vocab[preds[i][j].item()])
                    item_targs.append(self.dls.vocab[targs[i][j].item()])

            preds_list.append(item_preds)
            targets_list.append(item_targs)

        self.results += [ (res[0], res[1]) for res in zip(preds_list, targets_list) ]


    # --- validation begin/after phases ---
    def before_validate(self): self.results = []

    def after_validate(self):
        if (len(self.results) < 1): return

        preds, targs = map(list, zip(*self.results))
        for k in self.custom_metrics_dict.keys():
            self.custom_metrics_dict[k] = calculate_token_class_metrics(targs, preds, metric_key=k)

        try:
            self.learn.token_classification_report = calculate_token_class_metrics(targs,
                                                                                   preds,
                                                                                   'classification_report')
        except ZeroDivisionError as err:
            print(f'Couldn\'t calcualte classification report: {err}')


    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key): return self.custom_metrics_dict[metric_key]

# Cell
@typedispatch
def show_results(x:HF_TokenClassInput, y:HF_TokenTensorCategory, samples, outs, learner,
                 ctxs=None, max_n=6, **kwargs):
    # grab tokenizer
    hf_textblock_tfm = learner.dls.before_batch[0]
    hf_tokenizer = hf_textblock_tfm.hf_tokenizer

    res = L()
    for inp, trg, sample, pred in zip(x, y, samples, outs):
        # recontstruct the string and split on space to get back your pre-tokenized list of tokens
        toks = hf_tokenizer.convert_ids_to_tokens(inp, skip_special_tokens=True)
        pretokenized_toks =  hf_tokenizer.convert_tokens_to_string(toks).split()

        # get predictions for subtokens that aren't ignored (e.g. special toks and token parts)
        pred_labels = [ pred_lbl for lbl_id, pred_lbl in zip(trg, ast.literal_eval(pred[0])) if lbl_id != -100 ]

        trg_labels = ast.literal_eval(sample[1])
        res.append([f'{[ (tok, trg, pred) for tok, pred, trg in zip(pretokenized_toks, pred_labels, trg_labels) ]}'])

    display_df(pd.DataFrame(res, columns=['token / target label / predicted label'])[:max_n])
    return ctxs

# Cell
@patch
def blurr_predict_tokens(self:Learner, inp, **kargs):
    """Remove all the unnecessary predicted tokens after calling `Learner.predict`, so that you only
    get the predicted labels, label ids, and probabilities for what you passed into it in addition to the input
    """
    pred_lbls, pred_lbl_ids, probs = self.blurr_predict(inp)

    # grab the huggingface tokenizer from the learner's dls.tfms
    hf_textblock_tfm = self.dls.before_batch[0]
    hf_tokenizer = hf_textblock_tfm.hf_tokenizer
    tok_kwargs = hf_textblock_tfm.tok_kwargs

    # calculate the number of subtokens per raw/input token so that we can determine what predictions to
    # return
    subtoks_per_raw_tok = [ (entity, len(hf_tokenizer.tokenize(str(entity)))) for entity in inp ]

    # very similar to what HF_BatchTransform does with the exception that we are also grabbing
    # the `special_tokens_mask` to help with getting rid or irelevant predicts for any special tokens
    # (e.g., [CLS], [SEP], etc...)
    res = hf_tokenizer(inp, None,
                       max_length=hf_textblock_tfm.max_length,
                       padding=hf_textblock_tfm.padding,
                       truncation=hf_textblock_tfm.truncation,
                       is_split_into_words=hf_textblock_tfm.is_split_into_words,
                       **tok_kwargs)

    special_toks_msk = L(res['special_tokens_mask'])
    actual_tok_idxs = special_toks_msk.argwhere(lambda el: el != 1)

    # using the indexes to the actual tokens, get that info from the results returned above
    pred_lbls_list = ast.literal_eval(pred_lbls)
    actual_pred_lbls = L(pred_lbls_list)[actual_tok_idxs]
    actual_pred_lbl_ids = pred_lbl_ids[actual_tok_idxs]
    actual_probs = probs[actual_tok_idxs]

    # now, because a raw token can be mapped to multiple subtokens, we need to build a list of indexes composed
    # of the *first* subtoken used to represent each raw token (that is where the prediction is)
    offset = 0
    raw_trg_idxs = []
    for idx, (raw_tok, sub_tok_count) in enumerate(subtoks_per_raw_tok):
        raw_trg_idxs.append(idx+offset)
        offset += sub_tok_count-1 if (sub_tok_count > 1) else 0

    return inp, actual_pred_lbls[raw_trg_idxs], actual_pred_lbl_ids[raw_trg_idxs], actual_probs[raw_trg_idxs]