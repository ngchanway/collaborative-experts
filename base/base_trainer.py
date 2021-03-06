import torch
from abc import abstractmethod
import re
import time
from numpy import inf
from logger import TensorboardWriter


class BaseTrainer:
    """ Base class for all trainers
    """
    def __init__(self, model, loss, metrics, optimizer, config):
        self.config = config
        self.logger = config.get_logger(
            'trainer', config['trainer']['verbosity'])

        # setup GPU device if available, move model into configured device
        self.device, device_ids = self._prepare_device(config['n_gpu'])
        self.model = model.to(self.device)
        if len(device_ids) > 1:
            self.model = torch.nn.DataParallel(model, device_ids=device_ids)

        self.loss = loss
        self.metrics = metrics
        self.optimizer = optimizer

        cfg_trainer = config['trainer']
        self.epochs = cfg_trainer['epochs']
        self.save_period = cfg_trainer['save_period']
        self.monitor = cfg_trainer.get('monitor', 'off')

        # configuration to monitor model performance and save best
        if self.monitor == 'off':
            self.mnt_mode = 'off'
            self.mnt_best = 0
        else:
            self.mnt_mode, self.mnt_metric = self.monitor.split()
            assert self.mnt_mode in ['min', 'max']

            self.mnt_best = inf if self.mnt_mode == 'min' else -inf
            self.early_stop = cfg_trainer.get('early_stop', inf)

        self.start_epoch = 1

        self.checkpoint_dir = config.save_dir

        # setup visualization writer instance
        self.writer = TensorboardWriter(
            config.log_dir, self.logger, cfg_trainer['tensorboard'])

        if config.resume is not None:
            self._resume_checkpoint(config.resume)

    @abstractmethod
    def _train_epoch(self, epoch):
        """Training logic for an epoch

        :param epoch: Current epoch number
        """
        raise NotImplementedError

    def train(self):
        """Full training logic
        """
        for epoch in range(self.start_epoch, self.epochs + 1):
            result = self._train_epoch(epoch)

            # save logged informations into log dict
            log = {'epoch': epoch}
            for key, value in result.items():
                if key == 'metrics':
                    log.update({mtr.__name__: value[i]
                                for i, mtr in enumerate(self.metrics)})
                elif key == 'val_metrics':
                    log.update({'val_' + mtr.__name__: value[i]
                                for i, mtr in enumerate(self.metrics)})
                elif key == 'nested_val_metrics':
                    # NOTE: currently only supports two layers of nesting
                    for subkey, subval in value.items():
                        for subsubkey, subsubval in subval.items():
                            log[f"val_{subkey}_{subsubkey}"] = subsubval
                else:
                    log[key] = value

            # print logged informations to the screen
            for key, value in log.items():
                self.logger.info('    {:15s}: {}'.format(str(key), value))

            # eval model according to configured metric, save best # ckpt as trained_model
            best = False
            if self.mnt_mode != 'off':
                try:
                    # check whether specified metric improved or not, according to
                    # specified metric(mnt_metric)
                    lower = log[self.mnt_metric] <= self.mnt_best
                    higher = log[self.mnt_metric] >= self.mnt_best
                    improved = (self.mnt_mode == 'min' and lower) or \
                               (self.mnt_mode == 'max' and higher)
                except KeyError:
                    msg = "Warning: Metric '{}' not found, perf monitoring is disabled."
                    self.logger.warning(msg.format(self.mnt_metric))
                    self.mnt_mode = 'off'
                    improved = False
                    not_improved_count = 0

                if improved:
                    self.mnt_best = log[self.mnt_metric]
                    not_improved_count = 0
                    best = True
                else:
                    not_improved_count += 1

                if not_improved_count > self.early_stop:
                    self.logger.info("Val performance didn\'t improve for {} epochs. "
                                     "Training stops.".format(self.early_stop))
                    break

            # If checkpointing is done intermittently, still save models that outperform
            # the best metric
            save_best = best and not self.mnt_metric == "epoch"

            # Due to the fast runtime/slow HDD combination, checkpointing can dominate
            # the total training time, so we optionally skip checkpoints for some of
            # the first epochs
            if epoch < self.skip_first_n_saves:
                msg = f"Skipping ckpt save at epoch {epoch} <= {self.skip_first_n_saves}"
                self.logger.info(msg)
                continue

            if epoch % self.save_period == 0 or save_best:
                self._save_checkpoint(epoch, save_best=best)
            if epoch > self.num_keep_ckpts:
                self.purge_stale_checkpoints()

    def purge_stale_checkpoints(self):
        """Remove checkpoints that are no longer neededself.

        NOTE: This function assumes that the `best` checkpoint has already been renamed
        to have a format that differs from `checkpoint-epoch<num>.pth`
        """
        all_ckpts = list(self.checkpoint_dir.glob("*.pth"))
        found_epoch_ckpts = list(self.checkpoint_dir.glob("checkpoint-epoch*.pth"))
        if len(all_ckpts) <= self.num_keep_ckpts:
            return

        msg = "Expected at the best checkpoint to have been renamed to a different format"
        if not len(all_ckpts) > len(found_epoch_ckpts):
            import ipdb; ipdb.set_trace()
        assert len(all_ckpts) > len(found_epoch_ckpts), msg

        # purge the oldest checkpoints
        regex = r".*checkpoint-epoch(\d+)[.]pth$"
        epochs = [int(re.search(regex, str(x)).groups()[0]) for x in found_epoch_ckpts]
        sorted_ckpts = sorted(list(zip(epochs, found_epoch_ckpts)), key=lambda x: -x[0])

        for epoch, stale_ckpt in sorted_ckpts[self.num_keep_ckpts:]:
            tic = time.time()
            stale_ckpt.unlink()
            msg = f"removing stale ckpt [epoch {epoch}] [took {time.time() - tic:.2f}s]"
            self.logger.info(msg)

    def _prepare_device(self, n_gpu_use):
        """
        setup GPU device if available, move model into configured device
        """
        n_gpu = torch.cuda.device_count()
        if n_gpu_use > 0 and n_gpu == 0:
            self.logger.warning("Warning: There\'s no GPU available on this machine,"
                                "training will be performed on CPU.")
            n_gpu_use = 0
        if n_gpu_use > n_gpu:
            self.logger.warning("Warning: The number of GPU\'s configured to use is {}"
                                ", but only {} are available "
                                "on this machine.".format(n_gpu_use, n_gpu))
            n_gpu_use = n_gpu
        device = torch.device('cuda:0' if n_gpu_use > 0 else 'cpu')
        list_ids = list(range(n_gpu_use))
        return device, list_ids

    def _save_checkpoint(self, epoch, save_best=False):
        """Saving checkpoints

        :param epoch: current epoch number
        :param log: logging information of the epoch
        :param save_best: if True, rename the saved checkpoint to 'trained_model.pth'
        """
        arch = type(self.model).__name__
        state = {
            'arch': arch,
            'epoch': epoch,
            'state_dict': self.model.state_dict(),
            'monitor_best': self.mnt_best,
            'config': self.config
        }
        if self.include_optim_in_ckpts:
            state["optimizer"] = self.optimizer.state_dict()

        filename = str(self.checkpoint_dir /
                       'checkpoint-epoch{}.pth'.format(epoch))
        tic = time.time()
        self.logger.info("Saving checkpoint: {} ...".format(filename))
        torch.save(state, filename)
        self.logger.info(f"Done in {time.time() - tic:.3f}s")
        if save_best:
            self.logger.info("Updating 'best' checkpoint: {} ...".format(filename))
            best_path = str(self.checkpoint_dir / 'trained_model.pth')
            torch.save(state, best_path)
            self.logger.info(f"Done in {time.time() - tic:.3f}s")

    def _resume_checkpoint(self, resume_path):
        """ Resume from saved checkpoints

        :param resume_path: Checkpoint path to be resumed
        """
        resume_path = str(resume_path)
        self.logger.info("Loading checkpoint: {} ...".format(resume_path))
        checkpoint = torch.load(resume_path)
        self.start_epoch = checkpoint['epoch'] + 1
        self.mnt_best = checkpoint['monitor_best']

        # load architecture params from checkpoint.
        if checkpoint['config']['arch'] != self.config['arch']:
            msg = ("Warning: Architecture configuration given in config file is"
                   "different from that of checkpoint. This may yield an exception"
                   " while state_dict is being loaded.")
            self.logger.warning(msg)
        self.model.load_state_dict(checkpoint['state_dict'])

        if self.include_optim_in_ckpts:
            # load optimizer state from ckpt only when optimizer type is not changed.
            optim_args = checkpoint['config']['optimizer']
            if optim_args['type'] != self.config['optimizer']['type']:
                msg = ("Warning: Optimizer type given in config file differs from that"
                    " of checkpoint. Optimizer parameters not being resumed.")
                self.logger.warning(msg)
            else:
                self.optimizer.load_state_dict(checkpoint['optimizer'])

        self.logger.info(f"Ckpt loaded. Resume training from epoch {self.start_epoch}")
