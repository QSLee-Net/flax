# Copyright 2020 The Flax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SST-2 input pipeline."""

import collections
from typing import Dict, List, Set, Text, Tuple
import os

from absl import logging

import numpy as np
import tensorflow.compat.v2 as tf
import tensorflow_datasets as tfds


# pylint: disable=no-member


# pylint: disable=abstract-method
class OrderedCounter(collections.Counter, collections.OrderedDict):
  """A counter that remembers the order of added keys."""
  pass


def get_tokens(datasets: List[tf.data.Dataset]) -> Set[Text]:
  """Returns a set with all unique tokens in the given datasets."""
  counter = OrderedCounter()
  for ds in datasets:  # Data set already tokenized here.
    for example in tfds.as_numpy(ds):
      tokens = whitespace_tokenize(example['sentence'].strip())
      counter.update(tokens)
  sst_tokens = set(counter.keys())
  logging.info('Number of unique tokens: %d', len(sst_tokens))
  return sst_tokens


def save_vocab(vocab, path):
  """Saves a vocabulary to disk."""
  with open(path, mode='wb') as f:
    for token in vocab:
      f.write(token + b'\n')
  logging.info('Saved vocab to: %s', path)


def load_vocab(path):
  """Loads a vocabulary from disk."""
  vocab = collections.OrderedDict()
  with open(path, mode='rb') as f:
    for i, token in enumerate(f):
      vocab[token.rstrip(b'\n')] = i
  return vocab


def save_embed(embed, path):
  """Save word embeddings to disk."""
  assert path.endswith('.npz'), 'Path must end with .npz'
  np.savez(path, embed=embed)
  logging.info('Saved embed to: %s', path)


def load_embed(path):
  """Load word embeddings from disk."""
  return np.load(path)['embed']


# pylint: disable=dangerous-default-value
def get_glove_embeddings(
    datasets: List[tf.data.Dataset],
    glove_path: Text,
    glove_dim: int,
    specials: List[Text] = [b'<pad>', b'<unk>', b'<s>', b'</s>'],
    seed: int = 42,
    cache_dir: Text = None,
) -> Tuple[Dict[Text, int], np.ndarray]:
  """Filter Glove vectors to only use tokens that are in SST-2.

  The Glove file contains lines with the following format:

  ```
    cat 0.01 0.02 ... -0.02
    dog 0.02 0.03 ... -0.04
  ```
  That is, first the token, and then space-separated the value of each
  dimension.

  Args:
    datasets: A list of datasets to extract tokens from. We will only use
        embeddings for tokens in these datasets.
    glove_path: Path to GloVe word vectors.
    glove_dim: The size of the word vectors.
    specials: A list of special tokens (starting the vocabulary).
    seed: A seed to use to initialize the special token vectors.
    cache_dir: Directory to save/load the vocab and embeddings to/from.
      If it is empty, the vocab and embeddings will be saved.
      If both vocab and embeddings are found, they will be loaded.

  Returns:
    A tuple with the vocabulary (token -> ID) and the word embedding matrix.
  """
  sst_tokens = get_tokens(datasets)
  vectors = []
  vocab = collections.OrderedDict()
  np.random.seed(seed)

  # Special tokens.
  for token in specials:
    vectors.append(np.random.uniform(-0.05, 0.05, size=glove_dim))
    vocab[token] = len(vocab)
  vectors[0] = np.zeros_like(vectors[0])  # Zeros for padding.

  # Extract the embeddings for our set of tokens (discard the rest).
  with open(glove_path, mode='rb') as f:
    for line in f:
      parts = line.strip().rsplit(maxsplit=glove_dim)
      token = parts[0]
      if token in sst_tokens:
        vector = np.array(parts[1:], dtype=np.float32)
        vectors.append(vector)
        vocab[token] = len(vocab)

  embed = np.array(vectors)

  # Save vocab and embeddings to cache_dir.
  if cache_dir is not None:
    if not os.path.exists(cache_dir):
      os.mkdir(cache_dir)
    save_vocab(vocab, os.path.join(cache_dir, 'vocab.txt'))
    save_embed(embed, os.path.join(cache_dir, 'embed.npz'))

  return vocab, embed


def whitespace_tokenize(s: Text):
  """Splits ab input into tokens by whitspace."""
  return s.split()


def get_batches(
    train_ds: tf.data.Dataset,
    valid_ds: tf.data.Dataset,
    test_ds: tf.data.Dataset,
    seed: int = 0,
    batch_size: int = 64):
  """Returns batched versions of each dataset."""
  autotune = tf.data.experimental.AUTOTUNE

  # For shuffling we need to know how many training examples we have.
  num_train_examples = train_ds.reduce(np.int64(0), lambda x, _: x + 1).numpy()

  # We need to pad our batches since sentences have different lengths!
  # Sentences that are shorter in a batch will get 0s added at the end, until
  # all sentences in the batch have the same length.
  # padded_shapes says what kind of shapes to expect: [] means a scalar, [-1]
  # means a vector of variable length, and [1] means a vector of size 1.
  train_batches = train_ds.shuffle(
      num_train_examples,
      seed=seed,
      reshuffle_each_iteration=True).padded_batch(
          batch_size,
          padded_shapes={
              'idx': [], 'sentence': [-1], 'label': [1], 'length':[]},
          drop_remainder=True).prefetch(autotune)

  valid_batches = valid_ds.padded_batch(
      batch_size,
      padded_shapes={'idx': [], 'sentence': [-1], 'label': [1], 'length':[]},
      drop_remainder=False).prefetch(autotune)

  test_batches = test_ds.padded_batch(
      batch_size,
      padded_shapes={'idx': [], 'sentence': [-1], 'label': [1], 'length':[]},
      drop_remainder=False).prefetch(autotune)

  return train_batches, valid_batches, test_batches


class SST2DataSource(object):
  """Provides SST-2 data as pre-processed batches, a vocab, and embeddings."""

  def __init__(
      self,
      batch_size: int,
      glove_path: Text = None,
      glove_dim: int = 300,
      cache_dir: Text = None,
      vocab_path: Text = None,
      embed_path: Text = None,
      shuffle_seed: int = 1
  ):
    # Load SST-2 from TF datasets.
    data = tfds.load('glue/sst2')

    # Print an example.
    logging.info('Data sample: %s', next(tfds.as_numpy(data['train'].skip(4))))

    # Load vocabulary from disk if provided.
    if vocab_path is not None and os.path.exists(vocab_path):
      vocab = load_vocab(vocab_path)

    # Load embeddings from disk if provided.
    if embed_path is not None and os.path.exists(embed_path):
      embed = load_embed(embed_path)
    else:
      embed = None

    # Get a vocabulary and a corresponding Glove word embedding matrix.
    if vocab_path is None and embed_path is None:
      datasets = (data['train'], data['validation'], data['test'])
      vocab, embed = get_glove_embeddings(
          datasets, glove_path, glove_dim, cache_dir=cache_dir)

    unk_idx = vocab[b'<unk>']
    bos_idx = vocab[b'<s>']
    eos_idx = vocab[b'</s>']

    # Turn data examples into pre-processed examples by turning each sentence
    # into a sequence of token IDs. Also pre-prepend a beginning-of-sequence
    # token <s> and append an end-of-sequence token </s>.

    def tokenize(sentence: tf.Tensor):
      """Whitespace tokenize a single sentence."""
      return [whitespace_tokenize(sentence.numpy().strip())]

    def tf_tokenize(sentence: tf.Tensor):
      return  tf.py_function(tokenize, [sentence], Tout=tf.string)

    def encode(tokens: tf.Tensor):
      """Encodes a sequence of tokens (strings) into a sequence of token IDs."""
      return [[vocab[t] if t in vocab else unk_idx for t in tokens.numpy()]]

    def tf_encode(tokens: tf.Tensor):
      """Maps tokens to token IDs."""
      return tf.py_function(encode, [tokens], Tout=tf.int64)

    def tf_wrap_sequence(sequence: tf.Tensor):
      """Prepends BOS ID and appends EOS ID to a sequence of token IDs."""
      return tf.concat(([bos_idx], tf.concat((sequence, [eos_idx]), 0)), 0)

    def preprocess_example(example: Dict[Text, tf.Tensor]):
      example['sentence'] = tf_wrap_sequence(tf_encode(tf_tokenize(
          example['sentence'])))
      example['label'] = [example['label']]
      example['length'] = tf.shape(example['sentence'])[0]
      return example

    # Pre-process all datasets.
    self.train_ds = data['train'].map(preprocess_example).cache()
    self.valid_ds = data['validation'].map(preprocess_example).cache()
    self.test_ds = data['test'].map(preprocess_example).cache()

    # Make batches.
    self.train_batches, self.valid_batches, self.test_batches = get_batches(
        self.train_ds, self.valid_ds, self.test_ds,
        batch_size=batch_size, seed=shuffle_seed)

    self.vocab = vocab
    self.embed = embed
    self.vocab_size = len(vocab)

