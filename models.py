from itertools import cycle, islice

import tensorflow as tf


class FullyConnectedWTA:
    """Fully-connected winner-take-all autoencoder.

    This model is deterministic.
    """

    def __init__(self,
                 input_dim,
                 batch_size,
                 sparsity=0.05,
                 hidden_units=2,
                 encode_layers=3,
                 optimizer=tf.train.AdamOptimizer,
                 learning_rate=1e-2,
                 tie_weights=True,
                 weight_initializer=tf.random_normal_initializer(0, 0.01, seed=1),
                 bias_initializer=tf.constant_initializer(0.01),
                 name='FCWTA'):
        """Create the model.

        Args:
          input_dim: the dimensionality of the input data.
          batch_size: the batch size to be used.
          sparsity: the lifetime sparsity constraint to enforce.
          hidden_units: the number of units in each ReLU (encode) layer, and
            also the dimensionality of the encoded data.
          encode_layers: the number ReLU (encode) layers.
          optimizer: a TensorFlow optimizer op that takes only a learning rate.
          learning_rate: the learning rate to train with.
          tie_weights: whether to use the same weight matrix for the decode
            layer and final encode layer.
          weight_initializer: initializer to use for matrices of weights.
          bias_initializer: initializer to use for matrices of biases.
          name: the name of the variable scope to use.
        """
        self.input_dim = input_dim
        self.batch_size = batch_size
        self.sparsity = sparsity
        self.hidden_units = hidden_units
        self.encode_layers = encode_layers
        self.optimizer = optimizer
        self.learning_rate = learning_rate
        self.tie_weights = tie_weights
        self.weight_initializer = weight_initializer
        self.bias_initializer = bias_initializer
        self.name = name
        self._initialize_vars()

    def _initialize_vars(self):
        """Sets up the training graph."""
        with tf.variable_scope(self.name) as scope:
            self.global_step = tf.get_variable(
                'global_step',
                shape=[],
                initializer=tf.zeros_initializer())
            self.input = tf.placeholder(tf.float32, shape=[None, self.input_dim])

        current = self.input
        for i in range(self.encode_layers - 1):
            current = self._relu_layer(current, self.input_dim, self.input_dim, i)
        self.encoded = self._relu_layer(current, self.input_dim, self.hidden_units, self.encode_layers - 1)

        # Make batch size the last dimension (for use with tf.nn.top_k)
        encoded_t = tf.transpose(self.encoded)

        # Compute the indices corresponding to the top k activations for each
        # neuron in the final encoder layer
        k = int(self.sparsity * self.batch_size)
        _, top_indices = tf.nn.top_k(encoded_t, k=k, sorted=False)

        # Transform top_indices, which contains rows of column indices, into
        # indices, a list of [row, column] pairs (for use with tf.scatter_nd)
        top_k_unstacked = tf.unstack(top_indices, axis=1)
        row_indices = [tf.range(self.hidden_units) for _ in range(k)]
        combined_columns = tf.transpose(tf.stack(_interleave(row_indices, top_k_unstacked)))
        indices = tf.reshape(combined_columns, [-1, 2])

        # Apply sparsity constraint
        updates = tf.ones(self.hidden_units * k)
        shape = tf.constant([self.hidden_units, self.batch_size])
        mask = tf.scatter_nd(indices, updates, shape)
        sparse_encoded = self.encoded * tf.transpose(mask)

        self.decoded = self._decode_layer(sparse_encoded)

        self.loss = tf.reduce_sum(tf.square(self.decoded - self.input))
        self.optimizer_op = self.optimizer(self.learning_rate).minimize(
            self.loss, self.global_step)

        self.saver = tf.train.Saver(tf.global_variables())

    def _relu_layer(self, input, input_dim, output_dim, layer_num):
        with tf.variable_scope(self.name) as scope:
            return tf.nn.relu_layer(
                input,
                tf.get_variable('encode_W_{}'.format(layer_num),
                                shape=[input_dim, output_dim],
                                initializer=self.weight_initializer),
                tf.get_variable('encode_b_{}'.format(layer_num),
                                shape=[output_dim],
                                initializer=self.bias_initializer),
                'encode_layer_{}'.format(layer_num))

    def _decode_layer(self, input, reuse=False):
        with tf.variable_scope(self.name, reuse=reuse) as scope:
            decode_b = tf.get_variable('decode_b',
                                       shape=[self.input_dim],
                                       initializer=self.bias_initializer)
            if self.tie_weights:
                scope.reuse_variables()
                decode_W = tf.transpose(tf.get_variable(
                    self._get_last_encode_layer_name(),
                    shape=[self.input_dim, self.hidden_units]))
            else:
                decode_W = tf.get_variable(
                    'decode_W',
                    shape=[self.hidden_units, self.input_dim],
                    initializer=self.weight_initializer)
            return tf.matmul(input, decode_W) + decode_b

    def _get_last_encode_layer_name(self):
        return 'encode_W_{}'.format(self.encode_layers - 1)

    def step(self, session, input, forward_only=False):
        """Run a step of the model, feeding the given inputs.

        Args:
          session: TensorFlow session to use.
          input: NumPy array to feed as input.
          forward_only: whether to do the backward step or only forward.

        Returns:
          A tuple containing the reconstruction and the (summed) squared loss.

        Raises:
          ValueError: if batch size (resp. dimensionality) of input does not
          agree with the batch_size (resp. input_dim) provided in the
          constructor.
        """
        if input.shape[0] != self.batch_size:
            raise ValueError('Input batch size must equal the batch_size '
                             'provided in the constructor, {} != {}.'.format(
                                input.shape[0], self.batch_size))
        if input.shape[1] != self.input_dim:
            raise ValueError('Dimensionality of input must equal the input_dim '
                             'provided in the constructor, {} != {}.'.format(
                                input.shape[1], self.input_dim))

        if forward_only:
            decoded, loss = session.run(
                [self.decoded, self.loss],
                feed_dict={self.input: input})
        else:
            decoded, loss, _ = session.run(
                [self.decoded, self.loss, self.optimizer_op],
                feed_dict={self.input: input})
        return decoded, loss

    def encode(self, session, input):
        """Encode the given inputs.

        Args:
          session: TensorFlow session to use.
          input: NumPy array to feed as input.

        Returns:
          The encoded data, with shape (input.shape[1], hidden_units).

        Raises:
          ValueError: if dimensionality of input disagrees with the input_dim
          provided in the constructor.
        """
        if input.shape[1] != self.input_dim:
            raise ValueError('Dimensionality of input must equal the input_dim'
                             'provided in the constructor, {} != {}.'.format(
                                input.shape[1], self.input_dim))
        return session.run(self.encoded, feed_dict={self.input: input})

    def get_dictionary(self, session):
        """Fetch (approximately) the learned code dictionary.

        Args:
          session: TensorFlow session to use.

        Returns:
          The code dictionary, with shape (hidden_units, input_dim).
        """
        fake_input = 1e15 * tf.eye(self.hidden_units)
        return session.run(self._decode_layer(fake_input, reuse=True))


def _interleave(xs, ys):
    """Interleaves the two given lists (assumed to be of equal length)."""
    return [val for pair in zip(xs, ys) for val in pair]
