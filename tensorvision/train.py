#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Trains, evaluates and saves the model network using a queue."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import imp
import json
import logging
import numpy as np
import os.path
import sys

# configure logging
if 'TV_IS_DEV' in os.environ and os.environ['TV_IS_DEV']:
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=logging.INFO,
                        stream=sys.stdout)
else:
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=logging.INFO,
                        stream=sys.stdout)


import time

from shutil import copyfile

from six.moves import xrange  # pylint: disable=redefined-builtin

import tensorflow as tf

import string

import tensorvision.utils as utils
import tensorvision.core as core

flags = tf.app.flags
FLAGS = flags.FLAGS


def _copy_parameters_to_traindir(hypes, input_file, target_name, target_dir):
    """
    Helper to copy files defining the network to the saving dir.

    Parameters
    ----------
    input_file : str
        name of source file
    target_name : str
        target name
    traindir : str
        directory where training data is saved
    """
    target_file = os.path.join(target_dir, target_name)
    input_file = os.path.os.path.realpath(
        os.path.join(hypes['dirs']['base_path'], input_file))
    copyfile(input_file, target_file)


def initialize_training_folder(hypes):
    """
    Creating the training folder and copy all model files into it.

    The model will be executed from the training folder and all
    outputs will be saved there.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    """
    target_dir = os.path.join(hypes['dirs']['output_dir'], "model_files")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    # Creating an additional logging saving the console outputs
    # into the training folder
    logging_file = os.path.join(hypes['dirs']['output_dir'], "output.log")
    utils.create_filewrite_handler(logging_file)

    # TODO: read more about loggers and make file logging neater.

    target_file = os.path.join(target_dir, 'hypes.json')
    with open(target_file, 'w') as outfile:
        json.dump(hypes, outfile, indent=2)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['input_file'], "data_input.py", target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['architecture_file'], "architecture.py",
        target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['objective_file'], "objective.py", target_dir)
    _copy_parameters_to_traindir(
        hypes, hypes['model']['optimizer_file'], "solver.py", target_dir)


def maybe_download_and_extract(hypes):
    """
    Download the data if it isn't downloaded by now.

    Parameters
    ----------
    hypes : dict
        Hyperparameters
    """
    f = os.path.join(hypes['dirs']['base_path'], hypes['model']['input_file'])
    data_input = imp.load_source("input", f)
    if hasattr(data_input, 'maybe_download_and_extract'):
        data_input.maybe_download_and_extract(hypes, hypes['dirs']['data_dir'])


def _write_evaluation_to_summary(evaluation_results, summary_writer, phase,
                                 global_step, sess):
    """
    Write the evaluation_results to the summary file.

    Parameters
    ----------
    evaluation_results : tuple
        The output of do_eval
    summary_writer : tf.train.SummaryWriter
    phase : string
        Name of Operation to write
    global_step : tensor or int
        Xurrent training step
    sess : tf.Session
    """
    # write result to summary
    summary = tf.Summary()
    eval_names, avg_results = evaluation_results
    for name, result in zip(eval_names, avg_results):
        summary.value.add(tag='Evaluation/' + phase + '/' + name,
                          simple_value=result)
    summary_writer.add_summary(summary, global_step)


def _do_evaluation(hypes, step, sess_coll, eval_dict):
    sess, saver, summary_op, summary_writer, coord, threads = sess_coll
    logging.info('Doing Evaluate with Training Data.')

    eval_results = core.do_eval(hypes, eval_dict, phase='train',
                                sess=sess)
    _write_evaluation_to_summary(eval_results, summary_writer,
                                 "Train", step, sess)

    logging.info('Doing Evaluation with Testing Data.')
    eval_results = core.do_eval(hypes, eval_dict, phase='val',
                                sess=sess)
    _write_evaluation_to_summary(eval_results, summary_writer,
                                 'val', step, sess)


def _write_eval_dict_to_summary(eval_dict, tag, summary_writer, global_step):
    summary = tf.Summary()
    for name, result in eval_dict:
        summary.value.add(tag=tag + '/' + name,
                          simple_value=result)
    summary_writer.add_summary(summary, global_step)
    return


def _write_images_to_summary(images, summary_writer, step):
    for name, image in images:
        image = image.astype('float32')
        shape = image.shape
        image = image.reshape(1, shape[0], shape[1], shape[2])
        with tf.Graph().as_default():
            with tf.device('/cpu:0'):
                log_image = tf.image_summary(name, image)
            with tf.Session() as sess:
                summary_str = sess.run([log_image])
                summary_writer.add_summary(summary_str[0], step)
    return


def _do_python_evaluation(hypes, step, sess_coll, objective,
                          image_pl, softmax):
    logging.info('Doing Python Evaluation.')
    sess, saver, summary_op, summary_writer, coord, threads = sess_coll
    eval_dict, images = objective.evaluate(hypes, sess, image_pl, softmax)

    utils.print_eval_dict(eval_dict)
    _write_eval_dict_to_summary(eval_dict, summary_writer, step)
    _write_images_to_summary(images, summary_writer, step)

    return


def run_training(hypes, modules, tv_graph, tv_sess):
    """Run one iteration of training."""
    # Unpack operations for later use
    summary = tf.Summary()
    sess = tv_sess['sess']
    summary_writer = tv_sess['writer']

    solver = modules['solver']

    display_iter = hypes['logging']['display_iter']
    write_iter = hypes['logging'].get('write_iter', 5*display_iter)
    eval_iter = hypes['logging']['eval_iter']

    # Run the training Step
    start_time = time.time()
    for step in xrange(hypes['solver']['max_steps']):

        lr = solver.get_learning_rate(hypes, step)
        feed_dict = {tv_graph['learning_rate']: lr}

        if step % display_iter:
            sess.run([tv_graph['train_op']], feed_dict=feed_dict)

        # Write the summaries and print an overview fairly often.
        elif step % display_iter == 0:
            # Print status to stdout.
            _, loss_value = sess.run([tv_graph['train_op'],
                                      tv_graph['losses']['total_loss']],
                                     feed_dict=feed_dict)

            _print_training_status(hypes, step, loss_value, start_time, lr)

            eval_names, eval_ops = zip(*tv_graph['eval_list'])
            eval_results = sess.run(eval_ops, feed_dict=feed_dict)

            print_str = string.join([nam + ": %.2f" for nam in eval_names],
                                    ', ')
            print_str = "    " + print_str
            logging.info(print_str % tuple(eval_results))

            if step % write_iter == 0:
                # write values to summary
                summary_str = sess.run(tv_sess['summary_op'],
                                       feed_dict=feed_dict)
                summary_writer.add_summary(summary_str, global_step=step)
                summary.value.add(tag='training/total_loss',
                                  simple_value=float(loss_value))
                summary.value.add(tag='training/learning_rate',
                                  simple_value=lr)
                summary_writer.add_summary(summary, step)
                # Convert numpy types to simple types.
                eval_results = np.array(eval_results)
                eval_results = eval_results.tolist()
                eval_dict = zip(eval_names, eval_results)
                _write_eval_dict_to_summary(eval_dict, 'Eval_dict',
                                            summary_writer, step)

            # Reset timer
            start_time = time.time()

        # Do a evaluation and print the current state
        if (step + 1) % eval_iter == 0 or \
           (step + 1) == hypes['solver']['max_steps']:
            # write checkpoint to disk

            logging.info('Doing Python Evaluation.')
            eval_dict, images = modules['eval'].evaluate(
                hypes, sess, tv_graph['image_pl'], tv_graph['inf_out'])

            utils.print_eval_dict(eval_dict)
            _write_eval_dict_to_summary(eval_dict, 'Evaluation',
                                        summary_writer, step)
            _write_images_to_summary(images, summary_writer, step)

            # Reset timer
            start_time = time.time()

        # Save a checkpoint periodically.
        if (step + 1) % int(utils.cfg.step_write) == 0 or \
           (step + 1) == hypes['solver']['max_steps']:
            # write checkpoint to disk
            checkpoint_path = os.path.join(hypes['dirs']['output_dir'],
                                           'model.ckpt')
            tv_sess['saver'].save(sess, checkpoint_path, global_step=step)
            # Reset timer
            start_time = time.time()


def _print_training_status(hypes, step, loss_value, start_time, lr):

    info_str = utils.cfg.step_str

    # Prepare printing
    duration = (time.time() - start_time) / int(utils.cfg.step_show)
    examples_per_sec = hypes['solver']['batch_size'] / duration
    sec_per_batch = float(duration)

    logging.info(info_str.format(step=step,
                                 total_steps=hypes['solver']['max_steps'],
                                 loss_value=loss_value,
                                 lr_value=lr,
                                 sec_per_batch=sec_per_batch,
                                 examples_per_sec=examples_per_sec)
                 )


def do_training(hypes):
    """
    Train model for a number of steps.

    This trains the model for at most hypes['solver']['max_steps'].
    It shows an update every utils.cfg.step_show steps and writes
    the model to hypes['dirs']['output_dir'] every utils.cfg.step_eval
    steps.

    Paramters
    ---------
    hypes : dict
        Hyperparameters
    """
    # Get the sets of images and labels for training, validation, and
    # test on MNIST.

    modules = utils.load_modules_from_hypes(hypes)

    # Tell TensorFlow that the model will be built into the default Graph.
    with tf.Graph().as_default():

        # build the graph based on the loaded modules
        with tf.name_scope("Queues"):
            queue = modules['input'].create_queues(hypes, 'train')

        tv_graph = core.build_training_graph(hypes, queue, modules)

        # prepaire the tv session
        tv_sess = core.start_tv_session(hypes)
        sess = tv_sess['sess']

        with tf.name_scope('Validation'):
            image_pl = tf.placeholder(tf.float32)
            image = tf.expand_dims(image_pl, 0)
            inf_out = core.build_inference_graph(hypes, modules,
                                                 image=image)
            tv_graph['image_pl'] = image_pl
            tv_graph['inf_out'] = inf_out

        # Start the data load
        modules['input'].start_enqueuing_threads(hypes, queue, 'train', sess)

        # And then after everything is built, start the training loop.
        run_training(hypes, modules, tv_graph, tv_sess)

        # stopping input Threads
        tv_sess['coord'].request_stop()
        tv_sess['coord'].join(tv_sess['threads'])


def continue_training(logdir):
    """
    Continues training of a model.

    This will load model files and weights found in logdir and continues
    an aborted training.

    Parameters
    ----------
    logdir : string
        Directory with logs.
    """
    sess = None
    saver = None
    cur_step = core.load_weights(logdir, sess, saver)
    return cur_step


def main(_):
    """Run main function."""
    if FLAGS.hypes is None:
        logging.error("No hypes are given.")
        logging.error("Usage: tv-train --hypes hypes.json")
        exit(1)

    with open(tf.app.flags.FLAGS.hypes, 'r') as f:
        logging.info("f: %s", f)
        hypes = json.load(f)

    utils.set_gpus_to_use()
    utils.load_plugins()
    utils.set_dirs(hypes, tf.app.flags.FLAGS.hypes)

    logging.info("Initialize training folder")
    initialize_training_folder(hypes)
    maybe_download_and_extract(hypes)
    logging.info("Start training")
    do_training(hypes)


if __name__ == '__main__':
    tf.app.run()
