""" Decide which loss to update by Reinforce Learning """
# __Author__ == "Haowen Xu"
# __Data__ == "04-07-2018"

import os
import time
import math

import numpy as np
import tensorflow as tf
import tensorflow.contrib.slim as slim

from models.basic_model import Basic_model
import utils

logger = utils.get_logger()

class Controller(Basic_model):
    def __init__(self, config, exp_name='new_exp_ctrl'):
        super(Controller, self).__init__(config, exp_name)
        self._build_placeholder()
        self._build_graph()

    def _build_placeholder(self):
        config = self.config
        a = config.dim_output_ctrl
        s = config.dim_input_ctrl
        with self.graph.as_default():
            self.state_plh = tf.placeholder(shape=[None, s],
                                            dtype=tf.float32)
            self.reward_plh = tf.placeholder(shape=[None], dtype=tf.float32)
            self.action_plh = tf.placeholder(shape=[None, a], dtype=tf.int32)
            self.lr_plh = tf.placeholder(dtype=tf.float32)

    def _build_graph(self):
        config = self.config
        x_size = config.dim_input_ctrl
        h_size = config.dim_hidden_ctrl
        a_size = config.dim_output_ctrl
        lr = self.lr_plh
        with self.graph.as_default():
            model_name = config.controller_model_name
            initializer = tf.contrib.layers.xavier_initializer(uniform=True)
            if model_name == '2layer':
                hidden = slim.fully_connected(self.state_plh, h_size,
                                              weights_initializer=initializer,
                                              activation_fn=tf.nn.leaky_relu)
                self.logits = slim.fully_connected(hidden, a_size,
                                                   weights_initializer=initializer,
                                                   activation_fn=None)
                self.output = tf.nn.softmax(self.logits)
            elif model_name == '2layer_logits_clipping':
                hidden = slim.fully_connected(self.state_plh, h_size,
                                              weights_initializer=initializer,
                                              activation_fn=tf.nn.leaky_relu)
                self.logits = slim.fully_connected(hidden, a_size,
                                                   weights_initializer=initializer,
                                                   activation_fn=None)
                self.output = tf.nn.softmax(self.logits /
                                            config.logit_clipping_c)
            elif model_name == 'linear':
                self.logits = slim.fully_connected(self.state_plh, a_size,
                                                   weights_initializer=initializer,
                                                   activation_fn=None)
                self.output = tf.nn.softmax(self.logits)
            elif model_name == 'linear_logits_clipping':
                #self.logits = slim.fully_connected(self.state_plh, a_size,
                #                                   weights_initializer=initializer,
                #                                   activation_fn=None)
                # ----Old version----
                w = tf.get_variable('w', shape=[x_size, a_size], dtype=tf.float32,
                                    initializer=initializer)
                b = tf.get_variable('b', shape=[a_size], dtype=tf.float32,
                                    initializer=tf.zeros_initializer())
                self.logits = tf.matmul(self.state_plh, w) + b
                self.output = tf.nn.softmax(self.logits /
                                            config.logit_clipping_c)
            else:
                raise Exception('Invalid controller_model_name')

            self.chosen_action = tf.argmax(self.output, 1)
            self.action = tf.cast(tf.argmax(self.action_plh, 1), tf.int32)
            self.indexes = tf.range(0, tf.shape(self.output)[0])\
                * tf.shape(self.output)[1] + self.action
            self.responsible_outputs = tf.gather(tf.reshape(self.output, [-1]),
                                                self.indexes)
            self.loss = -tf.reduce_mean(tf.log(self.responsible_outputs)
                                        * self.reward_plh)

            # ----Restore gradients and update them after several iterals.----
            optimizer = tf.train.AdamOptimizer(learning_rate=lr)
            self.tvars = tf.trainable_variables()
            tvars = self.tvars
            self.gradient_plhs = []
            for idx, var in enumerate(tvars):
                placeholder = tf.placeholder(tf.float32, name=str(idx) + '_plh')
                self.gradient_plhs.append(placeholder)

            gvs = optimizer.compute_gradients(self.loss, tvars)
            self.grads = [grad for grad, _ in gvs]
            self.train_op = optimizer.apply_gradients(zip(self.gradient_plhs, tvars))
            #self.train_op = optimizer.apply_gradients(gvs)
            self.init = tf.global_variables_initializer()
            self.saver = tf.train.Saver()

    def sample(self, state, explore_rate=0.1):
        #
        # Sample an action from a given state, probabilistically

        # Args:
        #     state: shape = [dim_input_ctrl]
        #     explore_rate: explore rate

        # Returns:
        #     action: shape = [dim_output_ctrl]
        #
        sess = self.sess
        a_dist = sess.run(self.output, feed_dict={self.state_plh: [state]})
        a_dist = a_dist[0]
        # epsilon-greedy
        #if np.random.rand() < explore_rate:
        #    a = np.random.randint(len(a_dist))
        #else:
        #    a = np.argmax(a_dist)

        # continuous
        a = np.random.choice(a_dist, p=a_dist)
        a = np.argmax(a_dist == a)

        action = np.zeros(len(a_dist), dtype='i')
        action[a] = 1
        return action

    def train_one_step(self, transitions, lr):
        # Retrieve the gradients only for debugging, nothing special.
        gradients = self.get_gradients(transitions)
        print(gradients)
        feed_dict = dict(zip(self.gradient_plhs, gradients))
        feed_dict[self.lr_plh] = lr

        self.sess.run(self.train_op, feed_dict=feed_dict)

    def print_weights(self):
        sess = self.sess
        with self.graph.as_default():
            tvars = sess.run(self.tvars)
            for idx, var in enumerate(tvars):
                logger.info('idx:{}, var:{}'.format(idx, var))

    def get_weights(self):
        return self.sess.run(self.tvars)

    def get_gradients(self, transitions):
        """ Return the gradients according to one episode

        Args:
            sess: Current tf.Session
            state: shape = [time_steps, dim_input_ctrl]
            action: shape = [time_steps, dim_output_ctrl]
            reward: shape = [time_steps]

        Returns:
            grads: Gradients of all trainable variables
        """
        sess = self.sess
        reward = np.array([trans['reward'] for trans in transitions])
        action = np.array([trans['action'] for trans in transitions])
        state = np.array([trans['state'] for trans in transitions])
        feed_dict = {self.reward_plh: reward,
                     self.action_plh: action,
                     self.state_plh: state,
                    }
        grads = sess.run(self.grads, feed_dict=feed_dict)
        return grads
