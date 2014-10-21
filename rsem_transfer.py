#!/usr/bin/env python
# -*- coding: utf-8 -*

"""
This script will find out if enough space is available on remote cluster, if
there is, then it will find out the suitable GSMs, of which their sizes fit the
available space remotely and transfer them to remote by templating an rsync.sh
and execute it
"""

import os
import sys
import re
import glob
import stat
import yaml
import datetime
import logging.config

import paramiko
from jinja2 import Template

from args_parser import parse_args_for_rsem_transfer
from utils import execute_log_stdout_stderr, lockit, is_empty_dir, \
    pretty_usage, ugly_usage

sys.stdout.flush()          #flush print outputs to screen

# global variables: options, config, logger
options = parse_args_for_rsem_transfer()
try:
    with open(options.config_file) as inf:
        config = yaml.load(inf.read())
except IOError, _:
    print 'configuration file: {0} not found'.format(options.config_file)
    sys.exit(1)

logging.config.fileConfig(os.path.join(
    os.path.dirname(__file__), 'rsem_transfer.logging.config'))

logger = logging.getLogger('rsem_cron_transfer')


def sshexec(cmd, host, username, private_key_file='~/.ssh/id_rsa'):
    """
    ssh to username@remote and execute cmd.

    :param private_key_file: could be ~/.ssh/id_dsa, as well
    """

    private_key_file = os.path.expanduser(private_key_file)
    rsa_key = paramiko.RSAKey.from_private_key_file(private_key_file)

    # This step will timeout after about 75 seconds if cannot proceed
    channel = paramiko.Transport((host, 22))
    channel.connect(username=username, pkey=rsa_key)
    session = channel.open_session()

    # if exec_command fails, None will be returned
    session.exec_command(cmd)

    # not sure what -1 does? learned from ssh.py
    output = session.makefile('rb', -1).readlines()
    channel.close()
    if output:
        return output


def get_remote_free_disk_space(df_cmd, remote, username):
    """
    find the free disk space on remote host.

    :param df_cmd: should be in the form of df -k -P target_dir
    """
    output = sshexec(df_cmd, remote, username)
    # e.g. output:
    # ['Filesystem         1024-blocks      Used Available Capacity Mounted on\n',
    #  '/dev/analysis        16106127360 12607690752 3498436608      79% /extscratch\n']
    return int(output[1].split()[3]) * 1024


def estimate_current_remote_usage(remote, username, r_dir, l_dir):
    """
    estimate the space that has already been or will be consumed by rsem_output
    by walking through each GSM and computing the sum of their estimated usage,
    if rsem.COMPLETE exists for a GSM, then ignore that GSM

    mechanism: fetch the list of files in r_dir, and find the
    fastq.gz for each GSM, then find the corresponding fastq.gz in
    l_dir, and estimate sizes based on them

    :param find_cmd: should be in the form of find {remote_dir}
    :param r_dir: remote rsem output directory
    :param l_dir: local rsem output directory

    """
    find_cmd = 'find {0}'.format(r_dir)
    output = sshexec(find_cmd, remote, username)
    if output is None:
        raise ValueError(
            'cannot estimate current usage on remote host. please check '
            '{0} exists on {1}'.format(r_dir, remote))
    output = [_.strip() for _ in output] # remote trailing '\n'

    usage = 0
    for dir_ in sorted(output):
        match = re.search(r'(GSM\d+$)', os.path.basename(dir_))
        if match:
            rsem_comp = os.path.join(dir_, 'rsem.COMPLETE')
            if (not rsem_comp in output) and (not is_empty_dir(dir_, output)):
                # only count the disk spaces used by those GSMs that are
                # finished or processed successfully
                gsm_dir = dir_.replace(r_dir, l_dir)
                fq_gz_sizes = get_fq_gz_sizes(gsm_dir)
                usage += estimate_rsem_usage(fq_gz_sizes)
    return usage


def get_real_current_usage(remote, username, r_dir):
    """this will return real space consumed currently by rsem analysis"""
    output = sshexec('du -s {0}'.format(r_dir), remote, username)
    # e.g. output:
    # ['3096\t/path/to/top_outdir\n']
    usage = int(output[0].split('\t')[0]) * 1024 # in KB => byte
    return usage


def find_sras(gsm_dir):
    """glob sra files in the gsm_dir"""
    info_file = os.path.join(gsm_dir, 'sras_info.yaml')
    if os.path.exists(info_file):
        with open(info_file) as inf:
            info = yaml.load(inf.read())
            sra_files = [i for j in info for i in j.keys()
                         if os.path.exists(i)]
        return sorted(sra_files)
    

def find_fq_gzs(gsm_dir):
    """
    return a list of fastq.gz files for a GSM if sra2fastq.COMPLETE exists

    :param gsm_dir: the GSM directory, generated by os.walk
    """
    sras = find_sras(gsm_dir)
    if not sras:
        return
    sras = [os.path.basename(_) for _ in sras]
    nonexistent_flags = []
    for sra in sras:
        flag = os.path.join(gsm_dir, '{0}.sra2fastq.COMPLETE'.format(sra))
        if not os.path.exists(flag):
            nonexistent_flags.append(flag)

    if nonexistent_flags:       # meaning sra2fastq not completed yet
        logger.debug('sra2fastq not completed in {0}, '
                     'no fastq.gz files are returned. The following sra2fastq '
                     'flags are missing'.format(gsm_dir))
        for _ in nonexistent_flags:
            logger.debug('\t{0}'.format(_))
    else:
        fq_gzs = []
        files = os.listdir(gsm_dir)
        for _ in files:
            match = re.search(r'([SER]RR\d+)_[12]\.fastq\.gz', _, re.IGNORECASE)
            if match:
                fq_gzs.append(os.path.join(gsm_dir, match.group(0)))
        # e.g. fq_gzs:
        # ['/path/rsem_output/GSExxxxx/species/GSMxxxxxxx/SRRxxxxxxx_x.fastq.gz',
        #  '/path/rsem_output/GSExxxxx/species/GSMxxxxxxx/SRRxxxxxxx_x.fastq.gz']
        return fq_gzs


def estimate_rsem_usage(fq_gz_size):
    """
    estimate the maximum disk space that is gonna be consumed by rsem analysis
    on one GSM based on a list of fq_gzs

    :param fq_gz_size: a number reprsenting the total size of fastq.gz files
                       for the corresponding GSM
    """
    # Based on observation of smaller fastq.gz file by gunzip -l
    # compressed        uncompressed  ratio uncompressed_name
    # 266348960          1384762028  80.8% rsem_output/GSE42735/homo_sapiens/GSM1048945/SRR628721_1.fastq
    # 241971266          1255233364  80.7% rsem_output/GSE42735/homo_sapiens/GSM1048946/SRR628722_1.fastq

    # would be easier just incorporate this value into FASTQ2USAGE_RATIO, or
    # ignore it based on the observation of the size between fastq.gz and
    # *.temp
    # gzip_compression_ratio = 0.8

    fastq2usage_ratio = config['FASTQ2USAGE_RATIO']

    # estimate the size of uncompressed fastq
    # res = fq_gz_size / (1 - gzip_compression_ratio)
    res = fq_gz_size
    # overestimate
    res = res * fastq2usage_ratio
    return res


def get_gsms_transferred(record_file):
    """
    fetch the list of GSMs that have already been transferred from record_file
    """
    if not os.path.exists(record_file):
        return []
    else:
        with open(record_file) as inf:
            return [_.strip() for _ in inf if not _.strip().startswith('#')]


def append_transfer_record(gsm_to_transfer, record_file):
    """
    append the GSMs that have just beened transferred successfully to
    record_file
    """
    with open(record_file, 'ab') as opf:
        now = datetime.datetime.now()
        opf.write('# {0}\n'.format(now.strftime('%y-%m-%d %H:%M:%S')))
        for _ in gsm_to_transfer:
            opf.write('{0}\n'.format(_))


def write(transfer_script, template, **params):
    """
    template the qsub_rsync (for qsub e.g. on apollo thosts.q queue) or rsync
    (for execution directly (e.g. westgrid) script
    """
    # needs improvment to make it configurable
    input_file = os.path.join(template)

    with open(input_file) as inf:
        template = Template(inf.read())

    with open(transfer_script, 'wb') as opf:
        opf.write(template.render(**params))


def get_gse_species_gsm_from_path(path):
    """
    trying to capture info from directory like
    path/to/GSExxxxx/species/GSMxxxxx
    """
    gse_species_path, gsm = os.path.split(path)
    gse_path, species = os.path.split(gse_species_path)
    gse = os.path.basename(gse_path)
    return gse, species, gsm


def get_fq_gz_sizes(gsm_dir):
    """
    get sizes from <gsm_dir>/fq_gz_sizes.txt, if it doesn't exist, then create
    it
    """
    fq_gzs_info = os.path.join(gsm_dir, 'fq_gzs_info.yaml')
    if not os.path.exists(fq_gzs_info):
        create(fq_gzs_info, gsm_dir)
    with open(fq_gzs_info) as inf:
        info = yaml.load(inf.read())
        return sum(d[k]['size'] for d in info for k in d.keys())


def create(fq_gzs_info, gsm_dir):
    """create <gsm_dir>/fq_gz_sizes.txt"""
    fq_gzs = glob.glob(os.path.join(gsm_dir, '*.fastq.gz'))
    sizes = [os.path.getsize(_) for _ in fq_gzs]
    info = [{i: {'size': j, 'readable_size': pretty_usage(j)}}
            for (i, j) in zip(fq_gzs, sizes)]
    with open(fq_gzs_info, 'wb') as opf:
        yaml.dump(info, stream=opf, default_flow_style=False)
    logger.info('written {0}'.format(fq_gzs_info))


def select_samples_to_transfer(l_top_outdir, r_top_outdir,
                               r_host, r_username, gsms_transfer_record):
    """
    select samples to transfer (different from select_samples_to_process in
    utils_pre_pipeline.py, which are to process)
    """
    # r_: means relevant to remote host, l_: to local host

    r_free_space = get_remote_free_disk_space(config['REMOTE_CMD_DF'],
                                              r_host, r_username)
    logger.info(
        'r_free_space: {0}: {1}'.format(r_host, pretty_usage(r_free_space)))

    # r_real_current_usage is just for giving an idea of real usage on remote,
    # this variable is not utilized by following calculations, but the
    # corresponding current local usage is always real since there's no point
    # to estimate because only one process would be writing to the disk
    # simultaneously.
    r_real_current_usage = get_real_current_usage(
        r_host, r_username, r_top_outdir)
    logger.info('real current usage on {0} by {1}: {2}'.format(
        r_host, r_top_outdir, pretty_usage(r_real_current_usage)))

    r_est_current_usage = estimate_current_remote_usage(
        r_host, r_username, r_top_outdir, l_top_outdir)
    logger.info('estimated current usage (excluding samples with '
                'rsem.COMPLETE) on {0} by {1}: {2}'.format(
                    r_host, r_top_outdir, pretty_usage(r_est_current_usage)))
    r_max_usage = min(ugly_usage(config['REMOTE_MAX_USAGE']), r_free_space)
    logger.info('r_max_usage: {0}'.format(pretty_usage(r_max_usage)))
    r_min_free = ugly_usage(config['REMOTE_MIN_FREE'])
    logger.info('r_min_free: {0}'.format(pretty_usage(r_min_free)))
    r_free_to_use = min(r_max_usage - r_est_current_usage,
                        r_free_space - r_min_free)
    logger.info('r_free_to_use: {0}'.format(pretty_usage(r_free_to_use)))

    gsms = find_gsms_to_transfer(l_top_outdir, gsms_transfer_record, 
                                 r_free_to_use)
    return gsms


def find_gsms_to_transfer(l_top_outdir, gsms_transfer_record, r_free_to_use):
    """
    Walk through local top outdir, and for each GSMs, estimate its usage, and
    if it fits free_to_use space on remote host, count it as an element
    gsms_to_transfer
    """
    gsms_transferred = get_gsms_transferred(gsms_transfer_record)
    gsms_to_transfer = []
    # _, _ (dirs, files): ignored since they're not used
    for root, _, _ in os.walk(l_top_outdir):
        gse, _, gsm = get_gse_species_gsm_from_path(root)
        if not (re.search(r'GSM\d+$', gsm) and re.search(r'GSE\d+$', gse)):
            continue

        gsm_dir = root
        # use relpath for easy mirror between local and remote hosts
        transfer_id = os.path.relpath(gsm_dir, l_top_outdir)
        if transfer_id in gsms_transferred:
            logger.debug('{0} is in {1} already, ignore it'.format(
                transfer_id, gsms_transfer_record))
            continue

        sub_sh = os.path.relpath(
            os.path.join(gsm_dir, '0_submit.sh'), l_top_outdir)
        fq_gzs = find_fq_gzs(gsm_dir)
        # fq_gzs could be [] in cases when sra2fastq hasn't been completed yet
        if fq_gzs:
            if not os.path.exists(sub_sh):
                logger.warning('{0} doesn\'t exist, so skip {1}'.format(
                    sub_sh, transfer_id))
                continue
            fq_gz_sizes = get_fq_gz_sizes(gsm_dir)
            rsem_usage = estimate_rsem_usage(fq_gz_sizes)
            if rsem_usage < r_free_to_use:
                logger.info('{0} ({1}) fit remote free_to_use ({2})'.format(
                    transfer_id, pretty_usage(rsem_usage), 
                    pretty_usage(r_free_to_use)))
                gsms_to_transfer.append(transfer_id)
                r_free_to_use -= rsem_usage
        else:
            logger.debug('no fastq.gz files found in {0}'.format(gsm_dir))
    return gsms_to_transfer


@lockit(os.path.join(config['LOCAL_TOP_OUTDIR'], '.rsem_transfer'))
def main():
    """the main function"""
    l_top_outdir = config['LOCAL_TOP_OUTDIR']
    r_top_outdir = config['REMOTE_TOP_OUTDIR']
    r_host, r_username = config['REMOTE_HOST'], config['USERNAME']
    # different from processing in rsem_pipeline.py, here the completion is
    # marked by .COMPLETE flags, but by writting the completed GSMs to
    # gsms_transfer_record
    gsms_transfer_record = os.path.join(l_top_outdir, 'transferred_GSMs.txt')

    gsms = select_samples_to_transfer(l_top_outdir, r_top_outdir, r_host,
                                      r_username, gsms_transfer_record)
    if not gsms:
        logger.info('Cannot find a GSM that fits the current disk usage rule')
        return

    logger.info('GSMs to transfer:')
    for gsm in gsms:
        logger.info('\t{0}'.format(gsm))

    job_name = 'transfer.{0}'.format(
        datetime.datetime.now().strftime('%y-%m-%d_%H:%M:%S'))
    transfer_scripts_dir = os.path.join(l_top_outdir, 'transfer_scripts')
    if not os.path.exists(transfer_scripts_dir):
        os.mkdir(transfer_scripts_dir)

    # create transfer script
    transfer_script = os.path.join(
        transfer_scripts_dir, '{0}.sh'.format(job_name))

    write(transfer_script, options.rsync_template,
          job_name=job_name,
          username=r_username,
          hostname=r_host,
          gsms_to_transfer=gsms,
          local_top_outdir=l_top_outdir,
          remote_top_outdir=r_top_outdir)

    os.chmod(transfer_script, stat.S_IRUSR | stat.S_IWUSR| stat.S_IXUSR)
    rcode = execute_log_stdout_stderr(transfer_script)

    if rcode == 0:
        append_transfer_record(gsms, gsms_transfer_record)


if __name__ == "__main__":
    main()
