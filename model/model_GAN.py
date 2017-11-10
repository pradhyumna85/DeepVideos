import os
import sys
import shutil
import numpy as np

import tensorflow as tf
from tensorflow.python.ops import init_ops
from tensorflow.contrib.layers.python.layers import regularizers

module_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..")
if module_path not in sys.path:
    sys.path.append(module_path)
from datasets.batch_generator import datasets

slim = tf.contrib.slim
tf.reset_default_graph()
trunc_normal = lambda stddev: init_ops.truncated_normal_initializer(0.0, stddev)

# Contants
image_channels = 3
time_frames_to_consider = 4
time_frames_to_predict = 4
heigth_train= 64
width_train= 64
custom_test_size=[160,210]
heigth_test, width_test = custom_test_size
#===================================================================
#               Generative Model Parameters
#===================================================================
# +1 for input from previous layer !
scale_level_feature_maps= [[128, 256, 128, 3],
                           [128, 256, 128, 3],
                           [128, 256, 512, 256, 128, 3],
                           [128, 256, 512, 256, 128, 3]]
# as size of image increase in scaling ... conv layer increases !
scale_level_kernel_size = [ 
                            [3, 3, 3, 3],
                            [5, 3, 3, 5],
                            [5, 3, 3, 3, 3, 5],
                            [7, 5, 5, 5, 5, 7]
                          ]
#===================================================================
#               Descriminative Model Parameters
#===================================================================
disc_scale_level_feature_maps =   [[64],
                                   [64, 128, 128],
                                   [128, 256, 256],
                                   [128, 256, 512, 128]]
# kernel sizes for each convolution of each scale network in the discriminator model
disc_scale_level_kernel_size =  [[3],
                                [3, 3, 3],
                                [5, 5, 5],
                                [7, 7, 5, 5]]
# layer sizes for each fully-connected layer of each scale network in the discriminator model
# layer connecting conv to fully-connected is dynamically generated when creating the model
disc_fc_layer_units     = [[512, 256, 1],
                          [1024, 512, 1],
                          [1024, 512, 1],
                          [1024, 512, 1]]
#===================================================================
# regularizer !
l2_val = 0.00005
# Adam optimizer !
adam_learning_rate = 0.0004
# Tensorboard images to show
batch_size = 8
number_of_images_to_show = 4
assert number_of_images_to_show <= batch_size, "images to show should be less !"
timesteps=32
file_path = os.path.abspath(os.path.dirname(__file__))
data_folder = os.path.join(file_path, "../../data/")
log_dir_file_path = os.path.join(file_path, "../../logs/")
model_save_file_path = os.path.join(file_path, "../../checkpoint/")
output_video_save_file_path = os.path.join(file_path, "../../output/")
iterations = "iterations/"
best = "best/"
checkpoint_iterations = 100
best_model_iterations = 100
test_model_iterations = 5
best_loss = float("inf")
heigth, width = heigth_train, width_train
channels = 3
assert timesteps>=time_frames_to_consider and timesteps>=time_frames_to_predict, "time steps must be greater !"

#====================  COPIED CODE ===============================================
#
#  TENSORBOARD VISUALIZATION FOR SHARPNESS AND (Peak Signal to Noise Ratio){PSNR}
#=================================================================================
def log10(t):
    """
    Calculates the base-10 log of each element in t.
    @param t: The tensor from which to calculate the base-10 log.
    @return: A tensor with the base-10 log of each element in t.
    """
    numerator = tf.log(t)
    denominator = tf.log(tf.constant(10, dtype=numerator.dtype))
    return numerator / denominator
    
def psnr_error(gen_frames, gt_frames):
    """
    Computes the Peak Signal to Noise Ratio error between the generated images and the ground
    truth images.
    @param gen_frames: A tensor of shape [batch_size, height, width, 3]. The frames generated by the
                       generator model.
    @param gt_frames: A tensor of shape [batch_size, height, width, 3]. The ground-truth frames for
                      each frame in gen_frames.
    @return: A scalar tensor. The mean Peak Signal to Noise Ratio error over each frame in the
             batch.
    """
    shape = tf.shape(gen_frames)
    num_pixels = tf.to_float(shape[1] * shape[2] * shape[3])
    square_diff = tf.square(gt_frames - gen_frames)

    batch_errors = 10 * log10(1 / ((1 / num_pixels) * tf.reduce_sum(square_diff, [1, 2, 3])))
    return tf.reduce_mean(batch_errors)

def sharp_diff_error(gen_frames, gt_frames):
    """
    Computes the Sharpness Difference error between the generated images and the ground truth
    images.
    @param gen_frames: A tensor of shape [batch_size, height, width, 3]. The frames generated by the
                       generator model.
    @param gt_frames: A tensor of shape [batch_size, height, width, 3]. The ground-truth frames for
                      each frame in gen_frames.
    @return: A scalar tensor. The Sharpness Difference error over each frame in the batch.
    """
    shape = tf.shape(gen_frames)
    num_pixels = tf.to_float(shape[1] * shape[2] * shape[3])

    # gradient difference
    # create filters [-1, 1] and [[1],[-1]] for diffing to the left and down respectively.
    # TODO: Could this be simplified with one filter [[-1, 2], [0, -1]]?
    pos = tf.constant(np.identity(3), dtype=tf.float32)
    neg = -1 * pos
    filter_x = tf.expand_dims(tf.stack([neg, pos]), 0)  # [-1, 1]
    filter_y = tf.stack([tf.expand_dims(pos, 0), tf.expand_dims(neg, 0)])  # [[1],[-1]]
    strides = [1, 1, 1, 1]  # stride of (1, 1)
    padding = 'SAME'

    gen_dx = tf.abs(tf.nn.conv2d(gen_frames, filter_x, strides, padding=padding))
    gen_dy = tf.abs(tf.nn.conv2d(gen_frames, filter_y, strides, padding=padding))
    gt_dx = tf.abs(tf.nn.conv2d(gt_frames, filter_x, strides, padding=padding))
    gt_dy = tf.abs(tf.nn.conv2d(gt_frames, filter_y, strides, padding=padding))

    gen_grad_sum = gen_dx + gen_dy
    gt_grad_sum = gt_dx + gt_dy

    grad_diff = tf.abs(gt_grad_sum - gen_grad_sum)

    batch_errors = 10 * log10(1 / ((1 / num_pixels) * tf.reduce_sum(grad_diff, [1, 2, 3])))
    return tf.reduce_mean(batch_errors)

## =================== COPIED CODE ENDS ======================


def l2_loss(generated_frames, expected_frames):
    losses = []
    for each_scale_gen_frames, each_scale_exp_frames in zip(generated_frames, expected_frames):
        losses.append(tf.nn.l2_loss(tf.subtract(each_scale_gen_frames, each_scale_exp_frames)))
    
    loss = tf.reduce_mean(tf.stack(losses))
    return loss

def gdl_loss(generated_frames, expected_frames, alpha=2):
    """
    difference with side pixel and below pixel
    """
    scale_losses = []
    for i in xrange(len(generated_frames)):
        # create filters [-1, 1] and [[1],[-1]] for diffing to the left and down respectively.
        pos = tf.constant(np.identity(3), dtype=tf.float32)
        neg = -1 * pos
        filter_x = tf.expand_dims(tf.stack([neg, pos]), 0)  # [-1, 1]
        filter_y = tf.stack([tf.expand_dims(pos, 0), tf.expand_dims(neg, 0)])  # [[1],[-1]]
        strides = [1, 1, 1, 1]  # stride of (1, 1)
        padding = 'SAME'

        gen_dx = tf.abs(tf.nn.conv2d(generated_frames[i], filter_x, strides, padding=padding))
        gen_dy = tf.abs(tf.nn.conv2d(generated_frames[i], filter_y, strides, padding=padding))
        gt_dx = tf.abs(tf.nn.conv2d(expected_frames[i], filter_x, strides, padding=padding))
        gt_dy = tf.abs(tf.nn.conv2d(expected_frames[i], filter_y, strides, padding=padding))

        grad_diff_x = tf.abs(gt_dx - gen_dx)
        grad_diff_y = tf.abs(gt_dy - gen_dy)

        scale_losses.append(tf.reduce_sum((grad_diff_x ** alpha + grad_diff_y ** alpha)))

    # condense into one tensor and avg
    return tf.reduce_mean(tf.stack(scale_losses))

def total_loss(generated_frames, expected_frames, loss_from_disc, lambda_gdl=1.0, lambda_l2=1.0, lambda_disc=1.0):
    total_loss_cal = (lambda_gdl * gdl_loss(generated_frames, expected_frames) + 
                     lambda_l2 * l2_loss(generated_frames, expected_frames)+
                     lambda_disc * loss_from_disc)
    return total_loss_cal

#===================================================================
#                    Discriminator Model
#===================================================================
class ScaleBasedDiscriminator:
    def __init__(self, heigth, width, kernel_size, feature_maps, fc_layer_units, scale_number):
        assert len(feature_maps)==len(kernel_size), "Length should be equal !"
        self.heigth = heigth
        self.width = width
        self.kernel_size = kernel_size
        self.feature_maps = feature_maps
        self.fc_layer_units = fc_layer_units
        self.scale_number = scale_number
        self.input = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth, self.width, image_channels])
        self.create_graph()
        
    def create_graph(self):
        predication = self.input
        with tf.variable_scope('discriminator_scale_'+str(self.scale_number)):
            conv_counter = 0
            for index, (each_filter, each_kernel) in enumerate(zip(self.feature_maps, self.kernel_size)):
                with tf.variable_scope('conv_'+str(conv_counter)):
                    conv_counter += 1
                    stride = 1
                    # last layer stride 2 ... fc layer weights reduce ...
                    if index == (len(self.feature_maps)-1):
                        stride = 2
                    predication = slim.conv2d(predication, each_filter, [each_kernel, each_kernel],
                                              padding = 'VALID',
                                              stride = stride,
                                              weights_initializer=trunc_normal(0.01),
                                              weights_regularizer=regularizers.l2_regularizer(l2_val))
                    # print predication
            
            predication = slim.flatten(predication)
            # print predication
            
            fully_connected_counter = 0
            for index, each_layer_units in enumerate(self.fc_layer_units):
                with tf.variable_scope('fully_connected'+str(fully_connected_counter)):
                    fully_connected_counter += 1
                    activation = tf.nn.relu
                    # last layer sigmoid !
                    if index == (len(self.fc_layer_units)-1):
                        activation = tf.nn.sigmoid
                    predication = slim.fully_connected(predication, each_layer_units, activation_fn=activation)
                    # print predication
            # clip value between 0.1 and 0.9
            self.predication = tf.clip_by_value(predication, 0.1, 0.9)


class Discriminator:
    def __init__(self, heigth, width, disc_scale_level_feature_maps, disc_scale_level_kernel_size, disc_fc_layer_units):
        assert len(disc_scale_level_feature_maps)==len(disc_scale_level_kernel_size), "Length should be equal !"
        assert len(disc_scale_level_feature_maps)==len(disc_fc_layer_units), "Length should be equal !"
        
        self.heigth = heigth
        self.width = width
        self.disc_scale_level_feature_maps = disc_scale_level_feature_maps
        self.disc_scale_level_kernel_size = disc_scale_level_kernel_size
        self.disc_fc_layer_units = disc_fc_layer_units
        
        # ground truth image 
        self.ground_truth_images = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth, self.width, image_channels])
        # real or fake
        self.ground_truth_labels = tf.placeholder(dtype=tf.float32, shape=[None,1])
        
        self.len_scale = len(self.disc_scale_level_kernel_size)
        self.create_graph()
        self.loss()
        self.scale_images_ground_truth_for_inputs()
        self.tf_summary()
        
    def create_graph(self,):
        self.scale_based_discriminators = []
        for each_scale, (each_feature_map, each_kernel_size, each_fc_layer) in enumerate(zip(self.disc_scale_level_feature_maps, self.disc_scale_level_kernel_size, self.disc_fc_layer_units)):
            # scaling create [1/64, 1/32, 1/16, 1/4]
            scaling_factor = 1.0 / (2**(self.len_scale - 1 - each_scale))
            rescaled_heigth = int(scaling_factor * self.heigth)
            rescaled_width = int(scaling_factor * self.width)
            
            disc_at_scale = ScaleBasedDiscriminator(heigth=rescaled_heigth,
                                                    width=rescaled_width, kernel_size=each_kernel_size, 
                                                    feature_maps=each_feature_map, 
                                                    fc_layer_units=each_fc_layer, scale_number=each_scale)
            self.scale_based_discriminators.append(disc_at_scale)
            
        
        self.scaled_disc_predication = []
        for each_scaled_pred in self.scale_based_discriminators:
            self.scaled_disc_predication.append(each_scaled_pred.predication)
        # print self.scaled_disc_predication
        
    def loss(self):
        total_loss = []
        for each_scaled_op in self.scaled_disc_predication:
            # print each_scaled_op, self.ground_truth_labels
            curr_loss = tf.losses.sigmoid_cross_entropy(multi_class_labels=self.ground_truth_labels, logits=each_scaled_op)
            total_loss.append(curr_loss)
        
        self.dis_loss = tf.reduce_mean(tf.stack(total_loss))
        self.optimizer = tf.train.AdamOptimizer(adam_learning_rate)
        global_step = tf.Variable(0,name="dis_global_step_var",trainable=False)
        self.step = self.optimizer.minimize(self.dis_loss, global_step=global_step)
    
    def rescale_image(self, scaling_factor, heigth, width, ground_truths):
        """
        scaling_factor, heigth, width = values
        input_data, ground_truths = Tensors
        """
        rescaled_heigth = int(scaling_factor * heigth)
        rescaled_width = int(scaling_factor * width)
        assert rescaled_heigth != 0 and rescaled_width != 0, "scaling factor should not be zero !"
        ground_truths_reshaped = tf.image.resize_images(ground_truths, [rescaled_heigth, rescaled_width])
        return ground_truths_reshaped
    
    def scale_images_ground_truth_for_inputs(self,):
        inputs = []
        for each_scale in range(self.len_scale):
            scaling_factor = 1.0 / (2**(self.len_scale - 1 - each_scale))
            inputs.append(self.rescale_image(scaling_factor, self.heigth, self.width, self.ground_truth_images))
        self.rescaled_ground_truth_images = inputs
        # print inputs

    def tf_summary(self):
        train_loss = tf.summary.scalar("dis_train_loss", self.dis_loss)
        self.train_summary_merged = tf.summary.merge([train_loss])

#===================================================================
#                    Generative Model
#===================================================================
class GenerativeNetwork:
    def __init__(self, heigth_train, width_train, heigth_test, width_test, scale_level_feature_maps, scale_level_kernel_size):
        
        self.heigth_train = heigth_train
        self.width_train = width_train
        self.heigth_test = heigth_test
        self.width_test = width_test

        self.scale_level_feature_maps = scale_level_feature_maps
        self.scale_level_kernel_size = scale_level_kernel_size
        self.len_scale = len(self.scale_level_kernel_size)
        assert len(self.scale_level_feature_maps) == len(self.scale_level_kernel_size), "Length should be equal !"
        
        # Placeholders for inputs and outputs ... !
        self.input_train = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth_train, self.width_train, time_frames_to_consider * image_channels])
        self.output_train = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth_train, self.width_train, image_channels])
        self.input_test = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth_test, self.width_test, time_frames_to_consider * image_channels])
        self.output_test = tf.placeholder(dtype=tf.float32, shape=[None, self.heigth_test, self.width_test, image_channels])
        self.loss_from_disc = tf.placeholder(dtype=tf.float32, shape=[])
        
        self.each_scale_predication_train = []
        self.each_scale_ground_truth_train = []
        self.each_scale_predication_test = []
        self.each_scale_ground_truth_test = []
        
        self.create_graph(self.input_train, self.output_train, heigth_train, width_train, 
                          self.each_scale_predication_train, 
                          self.each_scale_ground_truth_train,
                          reuse=None)
        
        # reuse graph at time of test !
        self.create_graph(self.input_test, self.output_test, heigth_test, width_test, 
                          self.each_scale_predication_test,
                          self.each_scale_ground_truth_test,
                          reuse=True)
        
        self.loss()
        self.tf_summary()
        # print self.each_scale_predication_train
        # print self.each_scale_ground_truth_train
        # print self.each_scale_predication_test
        # print self.each_scale_ground_truth_test
        
    def rescale_image(self, scaling_factor, heigth, width, input_data, ground_truths, last_generated_frame):
        """
        scaling_factor, heigth, width = values
        input_data, ground_truths = Tensors
        """
        rescaled_heigth = int(scaling_factor * heigth)
        rescaled_width = int(scaling_factor * width)
        assert rescaled_heigth != 0 and rescaled_width != 0, "scaling factor should not be zero !"
        input_reshaped = tf.image.resize_images(input_data, [rescaled_heigth, rescaled_width])
        ground_truths_reshaped = tf.image.resize_images(ground_truths, [rescaled_heigth, rescaled_width])
        last_generated_frame_reshaped = None
        if last_generated_frame!=None:
            last_generated_frame_reshaped = tf.image.resize_images(last_generated_frame, [rescaled_heigth, rescaled_width])
        return (input_reshaped, ground_truths_reshaped, last_generated_frame_reshaped)
    
    def create_graph(self, input_data, ground_truths, heigth, width, 
                     predicated_at_each_scale_tensor, ground_truth_at_each_scale_tensor, reuse):
                
        # for each scale ... 
        for each_scale in range(self.len_scale):
            conv_counter = 0 
            with tf.variable_scope('scale_'+str(each_scale),reuse=reuse):
                # scaling create [1/64, 1/32, 1/16, 1/4]
                scaling_factor = 1.0 / (2**(self.len_scale - 1 - each_scale))
                last_generated_frame = None
                if each_scale > 0:
                    last_generated_frame = predicated_at_each_scale_tensor[each_scale-1]
                
                input_reshaped, ground_truths_reshaped, last_generated_frame_reshaped = self.rescale_image(scaling_factor, heigth, width, input_data, ground_truths, last_generated_frame)
                
                # append last scale output 
                if each_scale > 0:
                    input_reshaped = tf.concat([input_reshaped, last_generated_frame_reshaped],axis=3)
                
                # print (input_reshaped, ground_truths_reshaped)
                predication = input_reshaped
                
                # for each conv layers in that scale ... 
                feature_maps = scale_level_feature_maps[each_scale]
                kernel_size = scale_level_kernel_size[each_scale]
                
                assert len(feature_maps)==len(kernel_size), "Length should be equal !"
                for index, (each_filter, each_kernel) in enumerate(zip(feature_maps, kernel_size)): 
                    with tf.variable_scope('conv_'+str(conv_counter),reuse=reuse):
                        conv_counter += 1
                        activiation = tf.nn.relu
                        # last layer tanh !
                        if index==(len(kernel_size)-1):
                            activiation = tf.nn.tanh
                        predication = slim.conv2d(predication, each_filter, [each_kernel, each_kernel], 
                                              weights_initializer=trunc_normal(0.01),
                                              weights_regularizer=regularizers.l2_regularizer(l2_val),
                                              activation_fn=activiation)
                
                        
                # APPEND LAST GENERATED FRAME
                predicated_at_each_scale_tensor.append(predication)
                ground_truth_at_each_scale_tensor.append(ground_truths_reshaped)
    
    def loss(self):        
        # discriminator, gdl and l2 loss !
        self.combined_loss = total_loss(self.each_scale_predication_train, self.each_scale_ground_truth_train, self.loss_from_disc)
        self.optimizer = tf.train.AdamOptimizer(adam_learning_rate)
        global_step = tf.Variable(0,name="global_step_var",trainable=False)
        self.step = self.optimizer.minimize(self.combined_loss, global_step=global_step)

    def tf_summary(self):
        train_loss = tf.summary.scalar("gen_train_loss", self.combined_loss)
        val_loss = tf.summary.scalar("gen_val_loss", self.combined_loss)
        with tf.variable_scope('image_measures'):
            psnr_error_train = psnr_error(self.each_scale_predication_train[-1], self.output_train)
            psnr_error_train_s = tf.summary.scalar("train_psnr",psnr_error_train)
            psnr_error_val_s = tf.summary.scalar("val_psnr",psnr_error_train)


            sharpdiff_error_train = sharp_diff_error(self.each_scale_predication_train[-1],self.output_train)
            sharpdiff_error_train_s = tf.summary.scalar("train_shardiff",sharpdiff_error_train)
            sharpdiff_error_val_s = tf.summary.scalar("val_shardiff",sharpdiff_error_train)

            images_to_show_train = []
            images_to_show_val = []
            len_pred = len(self.each_scale_predication_train)
            for index_scale in range(len_pred-2,len_pred):
                images_to_show_train.append(tf.summary.image('train_output_scale_' + str(index_scale), self.each_scale_predication_train[index_scale],
                             number_of_images_to_show))
                images_to_show_train.append(tf.summary.image('train_ground_truth_scale_' + str(index_scale), self.each_scale_ground_truth_train[index_scale],
                             number_of_images_to_show))
                images_to_show_val.append(tf.summary.image('val_output_scale_' + str(index_scale), self.each_scale_predication_train[index_scale],
                             number_of_images_to_show))
                images_to_show_val.append(tf.summary.image('val_ground_truth_scale_' + str(index_scale), self.each_scale_ground_truth_train[index_scale],
                             number_of_images_to_show))
                

            psnr_error_test = psnr_error(self.each_scale_predication_test[-1], self.output_test)
            psnr_error_test_s = tf.summary.scalar("test_psnr",psnr_error_test)

            sharpdiff_error_test = sharp_diff_error(self.each_scale_predication_test[-1],self.output_test)
            sharpdiff_error_test_s = tf.summary.scalar("test_shardiff",sharpdiff_error_test)

            images_to_show_test = []
            len_pred = len(self.each_scale_predication_test)
            for index_scale in range(len_pred-2,len_pred):
                images_to_show_test.append(tf.summary.image('test_output_scale_' + str(index_scale), self.each_scale_predication_test[index_scale],
                             number_of_images_to_show))
                images_to_show_test.append(tf.summary.image('test_ground_truth_scale_' + str(index_scale), self.each_scale_ground_truth_test[index_scale],
                             number_of_images_to_show))

        self.train_summary_merged = tf.summary.merge([train_loss, psnr_error_train_s, sharpdiff_error_train_s]+images_to_show_train)
        self.test_summary_merged = tf.summary.merge([psnr_error_test_s, sharpdiff_error_test_s]+images_to_show_test)
        self.val_summary_merged = tf.summary.merge([val_loss, psnr_error_val_s, sharpdiff_error_val_s]+images_to_show_val)
        
# ======================== MODEL ENDS ========================

def log_directory_creation(sess):
    if tf.gfile.Exists(log_dir_file_path):
        tf.gfile.DeleteRecursively(log_dir_file_path)
    tf.gfile.MakeDirs(log_dir_file_path)

    # model save directory
    if os.path.exists(model_save_file_path):
        restore_model_session(sess, iterations + "gan_model")
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


def is_correct_batch_shape(X_batch, y_batch, info="train",heigth=heigth, width=width):
    # info can be {"train", "val"}
    if (X_batch is None or y_batch is None or
                X_batch.shape[1:] != (timesteps, heigth, width, channels) or
                y_batch.shape[1:] != (timesteps, heigth, width, channels)):
        print ("Warning: skipping this " + info + " batch because of shape")
        return False
    return True

def images_to_channels(X_batch):
    """
    This utility convert (Batch Size, TimeSteps, H, W, C) => (Batch Size, H, W, C, TimeSteps) =>  (Batch Size, H, W, C * TimeSteps)
    Refer Input of Mutli Scale Architecture !
    """
    input_data = X_batch.transpose(0,2,3,4,1)
    input_data = input_data.reshape(list(input_data.shape[:-2])+[-1]) 
    return input_data

def remove_oldest_image_add_new_image(X_batch,y_batch):
    """
    While frame predications each time step remove oldest image and newest image 
    """
    removed_older_image = X_batch[:,:,:,channels:]
    new_batch = np.append(removed_older_image, y_batch, axis=3)
    return new_batch

def alternate_disc_gen_training(sess, disc_model, gen_model, input_train, output_train):

    # get scaled input on ground truth image !
    rescaled_ground_truth_images = sess.run(disc_model.rescaled_ground_truth_images, feed_dict={disc_model.ground_truth_images: output_train})

    new_feed_dict = {}
    for i in range(len(rescaled_ground_truth_images)):
        new_feed_dict [ disc_model.scale_based_discriminators[i].input ] = rescaled_ground_truth_images[i]
    # real images !
    new_feed_dict[disc_model.ground_truth_labels] = np.ones([batch_size,1])

    # disc train on real data
    _, disc_summary_real = sess.run([disc_model.step, disc_model.train_summary_merged] ,feed_dict=new_feed_dict)

    # gen predict on real data => predicated
    each_scale_predication_train = sess.run(gen_model.each_scale_predication_train, feed_dict={gen_model.input_train : input_train, gen_model.output_train : output_train})

    new_feed_dict = {}
    for i in range(len(each_scale_predication_train)):
        new_feed_dict [ disc_model.scale_based_discriminators[i].input ] = each_scale_predication_train[i]
    # fake images !
    new_feed_dict[disc_model.ground_truth_labels] = np.zeros([batch_size,1])

    # disc train on predicated by gen
    _, disc_summary_fake, dis_loss = sess.run([disc_model.step, disc_model.train_summary_merged, disc_model.dis_loss] ,feed_dict=new_feed_dict)

    # gen take loss from disc and train 
    _, gen_summary = sess.run([gen_model.step, gen_model.train_summary_merged], feed_dict={gen_model.loss_from_disc : dis_loss, 
                                                                                           gen_model.input_train : input_train, 
                                                                                           gen_model.output_train : output_train
                                                                                          })

    return (disc_summary_real, disc_summary_fake, gen_summary)


def validation(sess, gen_model, data, val_writer, val_step):
    loss = []
    for X_batch, y_batch, _ in data.val_next_batch():
        if not is_correct_batch_shape(X_batch, y_batch, "val"):
            print ("validation batch is skipping ... ")            
            continue

        X_input = X_batch[:,:time_frames_to_consider]
        X_input = images_to_channels(X_input)
        # ground truth ... for loss calculation ... !
        output_train = X_batch[:,time_frames_to_consider,:,:,:]
        Y_output = np.zeros((batch_size,time_frames_to_predict,heigth,width,channels))
        for each_time_step in range(time_frames_to_predict):
            # gen predict on real data => predicated
            y_current_step, combined_loss, train_summary_merged = sess.run([gen_model.each_scale_predication_train[-1], gen_model.combined_loss,gen_model.val_summary_merged], feed_dict={gen_model.loss_from_disc : 0.0,
                                                                                                                gen_model.input_train : X_input, 
                                                                                                                gen_model.output_train : output_train})
            loss.append(combined_loss)
            val_writer.add_summary(train_summary_merged, val_step)
            val_step += 1
            Y_output[:,each_time_step,:,:,:] = y_current_step
            X_input = remove_oldest_image_add_new_image(X_input,y_current_step)
            output_train = X_batch[:,time_frames_to_predict+each_time_step+1,:,:,:]
    if len(loss)==0:
        return (val_step, float("inf"))
    return (val_step, sum(loss)/float(len(loss)))


def test(sess, gen_model, data, test_writer, test_step):
    for X_batch, y_batch, _ in data.get_custom_test_data():
        if not is_correct_batch_shape(X_batch, y_batch, "test",heigth=custom_test_size[0], width=custom_test_size[1]):
            print ("test batch is skipping ... ")            
            continue

        X_input = X_batch[:,:time_frames_to_consider]
        X_input = images_to_channels(X_input)
        # ground truth ... for loss calculation ... !
        output_train = X_batch[:,time_frames_to_consider,:,:,:]
        # Y_output = np.zeros((batch_size,time_frames_to_predict,heigth,width,channels))
        for each_time_step in range(time_frames_to_predict):
            # gen predict on real data => predicated
            y_current_step, test_summary_merged = sess.run([gen_model.each_scale_predication_test[-1], gen_model.test_summary_merged], feed_dict={gen_model.loss_from_disc : 0.0,
                                                                                                                gen_model.input_test : X_input, 
                                                                                                                gen_model.output_test : output_train})
            test_writer.add_summary(test_summary_merged, test_step)
            test_step += 1
            # Y_output[:,each_time_step,:,:,:] = y_current_step
            X_input = remove_oldest_image_add_new_image(X_input,y_current_step)
            output_train = X_batch[:,time_frames_to_predict+each_time_step+1,:,:,:]
    return test_step


def train():
    global best_loss
    with tf.Session() as sess:

        disc_model = Discriminator(heigth, width, disc_scale_level_feature_maps, disc_scale_level_kernel_size, disc_fc_layer_units)
        gen_model = GenerativeNetwork(heigth_train, width_train, heigth_test, width_test, scale_level_feature_maps, scale_level_kernel_size)
        
        # Initialize the variables (i.e. assign their default value)
        init = tf.global_variables_initializer()
        sess.run(init)

        # clear logs !
        log_directory_creation(sess)

        # summary !
        gen_train_writer = tf.summary.FileWriter(log_dir_file_path + "gen_train", sess.graph)
        des_train_writer = tf.summary.FileWriter(log_dir_file_path + "des_train", sess.graph)
        test_writer = tf.summary.FileWriter(log_dir_file_path + "test", sess.graph)
        val_writer = tf.summary.FileWriter(log_dir_file_path + "val", sess.graph)
        
        global_step = 0
        gen_count_iter = 0
        des_count_iter = 0
        val_count_iter = 0 
        test_count_iter = 0
        val_loss_seen = float("inf")

        while True:
            try:
                # data read iterator
                data = datasets(batch_size=batch_size, height=heigth, width=width, custom_test_size=custom_test_size)

                for X_batch, y_batch, _ in data.train_next_batch():
                    # print ("X_batch", X_batch.shape, "y_batch", y_batch.shape)
                    if not is_correct_batch_shape(X_batch, y_batch, "train"):
                        # global step not increased !
                        continue
                    for each_timesteps in range(time_frames_to_consider, timesteps-time_frames_to_consider):
                        
                        input_train = X_batch[:, each_timesteps-time_frames_to_consider:each_timesteps, :,:,:]
                        input_train = images_to_channels(input_train)

                        output_train = X_batch[:,each_timesteps,:,:,:]
                        
                        disc_summary_real, disc_summary_fake, gen_summary = alternate_disc_gen_training(sess, disc_model, gen_model, input_train, output_train)

                        gen_train_writer.add_summary(gen_summary, gen_count_iter)
                        gen_count_iter += 1
                        des_train_writer.add_summary(disc_summary_real, des_count_iter)
                        des_count_iter += 1                        
                        des_train_writer.add_summary(disc_summary_fake, des_count_iter)
                        des_count_iter += 1
                        
                    if global_step % checkpoint_iterations == 0:
                        save_model_session(sess, iterations + "gan_model")

                    if global_step % best_model_iterations == 0:
                        val_count_iter, curr_loss = validation(sess, gen_model, data, val_writer, val_count_iter)
                        if curr_loss < val_loss_seen:
                            val_loss_seen = curr_loss
                            save_model_session(sess, best + "gan_model")
                            
                    if global_step % test_model_iterations == 0:
                        test_count_iter = test(sess, gen_model, data, test_writer, test_count_iter)

                    print ("Iteration ", global_step, " best_loss ", val_loss_seen)
                    global_step += 1

            except:
                print ("error occur ... skipping ... !")

        train_writer.close()
        test_writer.close()


def main():
    train()

if __name__ == '__main__':
    main()
    