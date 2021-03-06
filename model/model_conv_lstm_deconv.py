# TensorFlow Model !
import os
import shutil
import numpy as np
import tensorflow as tf

tf.reset_default_graph()
from cell import ConvLSTMCell
import sys

module_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..")
if module_path not in sys.path:
    sys.path.append(module_path)
from datasets.batch_generator import datasets

slim = tf.contrib.slim
from tensorflow.python.ops import init_ops
from tensorflow.contrib.layers.python.layers import regularizers

trunc_normal = lambda stddev: init_ops.truncated_normal_initializer(0.0, stddev)
l2_val = 0.00005


class conv_lstm_deconv_model():
    def __init__(self):
        """Parameter initialization"""
        self.batch_size = 8
        self.number_of_images_to_show = 4
        self.timesteps = 32
        self.shape = [64, 64]  # Image shape
        self.kernel = [3, 3]
        self.channels = 3
        self.filters = [256, 256]  # 2 stacked conv lstm filters
        self.images_summary_timesteps = [0, 4, 16, 31]

        # Create a placeholder for videos.
        self.inputs = tf.placeholder(tf.float32, [self.batch_size, self.timesteps] + self.shape + [self.channels],
                                     name="conv_lstm_deconv_inputs")  # (batch_size, timestep, H, W, C)
        self.outputs_exp = tf.placeholder(tf.float32, [self.batch_size, self.timesteps] + self.shape + [self.channels],
                                          name="conv_lstm_deconv_outputs_exp")  # (batch_size, timestep, H, W, C)

        # model output
        self.model_output = None

        # loss
        self.l2_loss = None

        # optimizer
        self.optimizer = None

    def create_model(self):
        # conv layer reshape input
        conv_inp_reshape_size = [self.batch_size * self.timesteps, ] + self.shape + [self.channels, ]
        conv_input = tf.reshape(self.inputs, conv_inp_reshape_size)

        # conv before lstm
        with tf.variable_scope('conv_before_lstm'):
            net = slim.conv2d(conv_input, 32, [3, 3], scope='conv_1', weights_initializer=trunc_normal(0.01),
                              weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d(net, 64, [3, 3], stride=2, scope='conv_2', weights_initializer=trunc_normal(0.01),
                              weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d(net, 128, [3, 3], stride=2, scope='conv_3', weights_initializer=trunc_normal(0.01),
                              weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d(net, 256, [3, 3], scope='conv_4', weights_initializer=trunc_normal(0.01),
                              weights_regularizer=regularizers.l2_regularizer(l2_val))

        # back to lstm shape !
        net_output_shape = net.get_shape().as_list()
        lstm_reshape_size = [self.batch_size, self.timesteps] + net_output_shape[1:]
        lstm_reshape = tf.reshape(net, lstm_reshape_size)
        batch_size, time_step, H, W, C = lstm_reshape.get_shape().as_list()

        with tf.variable_scope('lstm_model'):
            cells = []
            for i, each_filter in enumerate(self.filters):
                cell = ConvLSTMCell([H, W], each_filter, self.kernel)
                cells.append(cell)

            cell = tf.nn.rnn_cell.MultiRNNCell(cells, state_is_tuple=True)
            states_series, current_state = tf.nn.dynamic_rnn(cell, lstm_reshape, dtype=lstm_reshape.dtype)
            # current_state => Not used ...
            model_output = states_series

        # reshape for conv transpose
        batch_size, time_step, H, W, C = model_output.get_shape().as_list()
        deconv_reshape = tf.reshape(model_output, [batch_size * time_step, H, W, C])

        with tf.variable_scope('deconv_after_lstm'):
            net = slim.conv2d_transpose(deconv_reshape, 256, [3, 3], scope='deconv_4',
                                        weights_initializer=trunc_normal(0.01),
                                        weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d_transpose(net, 128, [3, 3], scope='deconv_3', weights_initializer=trunc_normal(0.01),
                                        weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d_transpose(net, 64, [3, 3], stride=2, scope='deconv_2',
                                        weights_initializer=trunc_normal(0.01),
                                        weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d_transpose(net, 32, [3, 3], stride=2, scope='deconv_1',
                                        weights_initializer=trunc_normal(0.01),
                                        weights_regularizer=regularizers.l2_regularizer(l2_val))
            net = slim.conv2d_transpose(net, 3, [3, 3], activation_fn=tf.tanh, scope='deconv_0',
                                        weights_initializer=trunc_normal(0.01),
                                        weights_regularizer=regularizers.l2_regularizer(l2_val))

        net_pred_shape = net.get_shape().as_list()
        out_pred_shape = [batch_size, time_step, ] + net_pred_shape[1:]
        output_pred = tf.reshape(net, out_pred_shape)
        self.model_output = output_pred

    def images_summary(self):
        time_sliced_images = tf.slice(self.inputs, [0, 0, 0, 0, 0],
                                      [self.number_of_images_to_show, 1, self.shape[0], self.shape[1], self.channels])
        time_sliced_images = tf.squeeze(time_sliced_images,[1])
        tf.summary.image('input_images', time_sliced_images, self.number_of_images_to_show)
        for each_timestep in self.images_summary_timesteps:
            time_sliced_images = tf.slice(self.model_output, [0, each_timestep, 0, 0, 0],
                                          [self.number_of_images_to_show, 1, self.shape[0], self.shape[1], self.channels])
            time_sliced_images = tf.squeeze(time_sliced_images,[1])
            tf.summary.image('output_images_step_' + str(each_timestep), time_sliced_images,
                             self.number_of_images_to_show)

    def loss(self):
        frames_difference = tf.subtract(self.outputs_exp, self.model_output)
        batch_l2_loss = tf.nn.l2_loss(frames_difference)
        # divide by batch size ...
        l2_loss = tf.divide(batch_l2_loss, float(self.batch_size))
        self.l2_loss = l2_loss

    def optimize(self):
        train_step = tf.train.AdamOptimizer().minimize(self.l2_loss)
        self.optimizer = train_step

    def build_model(self):
        self.create_model()
        self.loss()
        self.optimize()
        self.images_summary()


file_path = os.path.abspath(os.path.dirname(__file__))
data_folder = os.path.join(file_path, "../../data/")
log_dir_file_path = os.path.join(file_path, "../../logs/")
model_save_file_path = os.path.join(file_path, "../../checkpoint/")
output_video_save_file_path = os.path.join(file_path, "../../output/")
iterations = "iterations/"
best = "best/"
checkpoint_iterations = 25
best_model_iterations = 25
best_l2_loss = float("inf")
heigth, width = 64, 64
channels = 3


def log_directory_creation(sess):
    if tf.gfile.Exists(log_dir_file_path):
        tf.gfile.DeleteRecursively(log_dir_file_path)
    tf.gfile.MakeDirs(log_dir_file_path)

    # model save directory
    if os.path.exists(model_save_file_path):
        restore_model_session(sess, iterations + "conv_lstm_deconv_model")
    else:
        os.makedirs(model_save_file_path + iterations)
        os.makedirs(model_save_file_path + best)

    # output dir creation
    if not os.path.exists(output_video_save_file_path):
        os.makedirs(output_video_save_file_path)


def save_model_session(sess, file_name):
    saver = tf.train.Saver()
    save_path = saver.save(sess, model_save_file_path + file_name)


def restore_model_session(sess, file_name):
    saver = tf.train.Saver()  # tf.train.import_meta_graph(model_save_file_path + file_name + ".meta")
    saver.restore(sess, model_save_file_path + file_name)
    print ("graph loaded!")


def is_correct_batch_shape(X_batch, y_batch, model, info="train"):
    # info can be {"train", "val"}
    if (X_batch is None or y_batch is None or
                X_batch.shape != (model.batch_size, model.timesteps, heigth, width, channels) or
                y_batch.shape != (model.batch_size, model.timesteps, heigth, width, channels)):
        print ("Warning: skipping this " + info + " batch because of shape")
        return False
    return True


def test():
    with tf.Session() as sess:
        model = conv_lstm_deconv_model()
        model.build_model()
        init = tf.global_variables_initializer()
        sess.run(init)

        log_directory_creation(sess)

        # data read iterator
        data = datasets(batch_size=model.batch_size, heigth=heigth, width=width)

        global_step = 0
        for X_batch, y_batch, filenames in data.test_next_batch():
            # print ("X_batch", X_batch.shape, "y_batch", y_batch.shape)
            if not is_correct_batch_shape(X_batch, y_batch, model, "test"):
                # global step not increased !
                continue

            input_data = np.zeros_like(X_batch)
            input_data[:, 0] = X_batch[:, 0]

            for i in range(model.timesteps):
                output_predicted = sess.run(model.model_output, feed_dict={model.inputs: input_data})
                if (i < (model.timesteps - 1)):
                    input_data[:, i + 1] = output_predicted[:, i]
                    print ("global step ", global_step, " time step ", i)

            data.frame_ext.generate_output_video(output_predicted, filenames)

            global_step += 1
            print ("test step ", global_step)


def train():
    global best_l2_loss
    with tf.Session() as sess:
        # conv lstm model
        model = conv_lstm_deconv_model()
        model.build_model()
        # Initialize the variables (i.e. assign their default value)
        init = tf.global_variables_initializer()
        sess.run(init)

        # clear logs !
        log_directory_creation(sess)

        # Tensorflow Summary
        tf.summary.scalar("train_l2_loss", model.l2_loss)
        summary_merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter(log_dir_file_path + "/train", sess.graph)
        test_writer = tf.summary.FileWriter(log_dir_file_path + "/test", sess.graph)
        global_step = 0

        while True:
            try:
                # data read iterator
                data = datasets(batch_size=model.batch_size, heigth=heigth, width=width)

                for X_batch, y_batch, _ in data.train_next_batch():
                    # print ("X_batch", X_batch.shape, "y_batch", y_batch.shape)
                    if not is_correct_batch_shape(X_batch, y_batch, model, "train"):
                        # global step not increased !
                        continue
                    _, summary = sess.run([model.optimizer, summary_merged], feed_dict={
                        model.inputs: X_batch, model.outputs_exp: y_batch})
                    # print ("summary ... ",global_step)
                    train_writer.add_summary(summary, global_step)

                    if global_step % checkpoint_iterations == 0:
                        save_model_session(sess, iterations + "conv_lstm_deconv_model")

                    if global_step % best_model_iterations == 0:
                        val_l2_loss_history = list()
                        batch_counter = 0
                        # iterate on validation batch ...
                        for X_val, y_val, _ in data.val_next_batch():
                            batch_counter += 1
                            # print ("X_val", X_val.shape, "y_val", y_val.shape)
                            if not is_correct_batch_shape(X_val, y_val, model, "val_" + str(batch_counter)):
                                continue
                            test_summary, val_l2_loss = sess.run([summary_merged, model.l2_loss],
                                                                 feed_dict={model.inputs: X_val,
                                                                            model.outputs_exp: y_val})
                            test_writer.add_summary(test_summary, global_step)
                            val_l2_loss_history.append(val_l2_loss)
                        temp_loss = sum(val_l2_loss_history) * 1.0 / len(val_l2_loss_history)

                        # save if better !
                        if best_l2_loss > temp_loss:
                            best_l2_loss = temp_loss
                            save_model_session(sess, best + "conv_lstm_deconv_model")

                    print ("Iteration ", global_step, " best_l2_loss ", best_l2_loss)
                    global_step += 1
            except:
                pass  # ignore problems and continue looping ...

        train_writer.close()
        test_writer.close()


def main():
    train()


if __name__ == '__main__':
    main()
