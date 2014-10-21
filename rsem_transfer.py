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
import subprocess
import logging.config
import argparse

import paramiko
from jinja2 import Template

from args_parser import parse_args_for_rsem_transfer
from utils import execute_log_stdout_stderr, pretty_usage, touch


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


def is_empty_dir(dir_, output):
    """
    test if dir_ is an empty dir based on output. If it's an empty dir, then
    there should be only one item is the list (output)
    """
    return len([_ for _ in output if dir_ in _]) == 1


def get_real_current_usage(remote, username, r_dir):
    """this will return real space consumed currently by rsem analysis"""
    output = sshexec('du -s {0}'.format(r_dir), remote, username)
    # e.g. output:
    # ['3096\t/path/to/top_outdir\n']
    usage = int(output[0].split('\t')[0]) * 1024 # in KB => byte
    return usage


def find_sras(gsm_dir):
    return sorted(glob.glob(
        os.path.join(gsm_dir,
        '[SED]RX[0-9]*[0-9]/[SED]RR[0-9]*[0-9]/[SED]RR[0-9]*[0-9].sra')))


def find_fq_gzs(gsm_dir):
    """
    return a list of fastq.gz files for a GSM if sra2fastq.COMPLETE exists

    :param gsm_dir: the GSM directory, generated by os.walk
    """
    sras = [os.path.basename(_) for _ in find_sras(gsm_dir)]
    nonexistent_flags = []
    for sra in sras:
        flag = os.path.join(gsm_dir, '{0}.sra2fastq.COMPLETE'.format(sra))
        if not os.path.exists(flag):
            nonexistent_flags.append(flag)

    if nonexistent_flags:       # meaning sra2fastq not completed yet
        logger.debug('sra2fastq not completed in {0}, '
                    'no fastq.gz files are returned. The following sra2fastq '
                    'flags are missing'.format(gsm_dir))
        for f in nonexistent_flags:
            logger.debug('\t{0}'.format(f))
    else:
        fq_gzs = []
        files = os.listdir(gsm_dir)
        for f in files:
            match = re.search(r'([SER]RR\d+)_[12]\.fastq\.gz', f, re.IGNORECASE)
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


def find_gsms_to_transfer(l_top_outdir, gsms_transfer_record,
                          r_free_to_use, r_min_free):
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
        if not os.path.exists(sub_sh):
            logger.debug(
                '{0} doesn\'t exist, so skip {1}'.format(sub_sh, transfer_id))
            continue

        fq_gzs = find_fq_gzs(gsm_dir)
        # fq_gzs could be [] in cases when sra2fastq hasn't been completed yet
        if fq_gzs:
            fq_gz_sizes = get_fq_gz_sizes(gsm_dir)
            rsem_usage = estimate_rsem_usage(fq_gz_sizes)
            if rsem_usage < r_free_to_use:
                logger.info(
                    '{0} ({1}) fit remote free_to_use ({2})'.format(
                        transfer_id, pretty_usage(rsem_usage),
                        pretty_usage(r_free_to_use)))
                gsms_to_transfer.append(transfer_id)
                r_free_to_use -= rsem_usage
                if r_free_to_use < r_min_free:
                    break
        else:
            logger.debug('no fastq.gz files found in {0}'.format(gsm_dir))
    return gsms_to_transfer


def get_fq_gz_sizes(gsm_dir):
    """
    get sizes from <gsm_dir>/fq_gz_sizes.txt, if it doesn't exist, then create
    it
    """
    sizes_file = os.path.join(gsm_dir, 'fq_gz_sizes.txt')
    if not os.path.exists(sizes_file):
        create(sizes_file, gsm_dir)
    with open(sizes_file) as inf:
        # e.g lines:
        # rsem_output/GSE48321/rattus_norvegicus/GSM1174815/SRR922253_1.fastq.gz    1027293853
        return sum([int(_.split()[1]) for _ in inf.readlines()
                    if not _.strip().startswith('#')])


def create(sizes_file, gsm_dir):
    """create <gsm_dir>/fq_gz_sizes.txt"""
    fq_gzs = glob.glob(os.path.join(gsm_dir, '*.fastq.gz'))

    sizes = []
    with open(sizes_file, 'wb') as opf:
        for f in fq_gzs:
            size = os.path.getsize(f)
            opf.write('{0}\t{1}\n'.format(f, size))
            opf.write('# {0}\t{1}\n'.format(f, pretty_usage(size)))
            sizes.append(size)

        opf.write('# Total\t{0}\n'.format(pretty_usage(sum(sizes))))
    logger.info('written {0}'.format(sizes_file))


def convert_disk_space(val):
    """convert human readable disk space to byte"""
    def err(val):
        raise ValueError(
            "Unreadable size: '{0}', make sure it's in the format "
            "of e.g. 1024 Byte|KB|MB|GB|TB (case insensitive)".format(val))
    sv = str(val).split()
    if len(sv) != 2:
        err(val)
    size = float(sv[0])
    unit = sv[1].lower()
    if unit == 'byte':
        return size
    elif unit == 'kb':
        return size * 2 ** 10
    elif unit == 'mb':
        return size * 2 ** 20
    elif unit == 'gb':
        return size * 2 ** 30
    elif unit == 'tb':
        return size * 2 ** 40
    else:
        err(val)


def get_lockers(l_top_outdir):
    # e.g. transfer script: transfer.14-08-18_12-17-55.sh.locker
    lockers = glob.glob(os.path.join(l_top_outdir, '.transfer.*sh.locker'))
    return lockers


def create_locker(locker):
    logger.info('creating {0}'.format(locker))
    touch(locker)


def remove_locker(locker):
    logger.info('removing {0}'.format(locker))
    os.remove(locker)


def main():
    """the main function"""
    # r_: means relevant to remote host, l_: to local host
    r_host, r_username = config['REMOTE_HOST'], config['USERNAME']
    l_top_outdir = config['LOCAL_TOP_OUTDIR']
    r_top_outdir = config['REMOTE_TOP_OUTDIR']

    lockers = get_lockers(l_top_outdir)
    if len(lockers) >= 1:
        logger.info(
            'The previous transfer hasn\'t completed yet ({0}), stop current '
            'session of rsem_cron_transfer.py'.format(' '.join(lockers)))
        return

    # create locker before estimate free space remotely
    job_name = 'transfer.{0}'.format(
        datetime.datetime.now().strftime('%y-%m-%d_%H-%M-%S'))
    transfer_scripts_dir = os.path.join(l_top_outdir, 'transfer_scripts')
    if not os.path.exists(transfer_scripts_dir):
        os.mkdir(transfer_scripts_dir)
    locker = os.path.join(l_top_outdir, '.{0}.sh.locker'.format(job_name))
    create_locker(locker)

    # now calculating different types of space
    r_free_space = get_remote_free_disk_space(
        config['REMOTE_CMD_DF'], r_host, r_username)
    logger.info('free space available on {0}: {1}'.format(
        r_host, pretty_usage(r_free_space)))

    r_estimated_current_usage = estimate_current_remote_usage(
        r_host, r_username, r_top_outdir, l_top_outdir)
    logger.info(
        'estimated current usage (excluding samples with '
        'rsem.COMPLETE) on {0} by {1}: {2}'.format(
            r_host, r_top_outdir, pretty_usage(r_estimated_current_usage)))

    # this is just for giving an idea of real usage on remote, this variable is
    # not utilized by following calculations
    r_real_current_usage = get_real_current_usage(
        r_host, r_username, r_top_outdir)
    logger.info('real current usage on {0} by {1}: {2}'.format(
        r_host, r_top_outdir, pretty_usage(r_real_current_usage)))

    r_max_usage = min(convert_disk_space(config['REMOTE_MAX_USAGE']),
                      r_free_space)
    logger.info('r_max_usage: {0}'.format(pretty_usage(r_max_usage)))
    r_min_free = convert_disk_space(config['REMOTE_MIN_FREE'])
    logger.info('r_min_free: {0}'.format(pretty_usage(r_min_free)))
    r_free_to_use = max(0, r_max_usage - r_estimated_current_usage)
    logger.info('free to use: {0}'.format(pretty_usage(r_free_to_use)))

    if r_free_to_use < r_min_free:
        logger.info(
            'free to use space ({0}) < min free ({1}) on {2}, '
            'no transfer is happening'.format(
                pretty_usage(r_free_to_use), pretty_usage(r_min_free), r_host))
        remove_locker(locker)
        return

    gsms_transfer_record = os.path.join(l_top_outdir, 'transferred_GSMs.txt')
    gsms_to_transfer = find_gsms_to_transfer(
        l_top_outdir, gsms_transfer_record, r_free_to_use, r_min_free)

    if not gsms_to_transfer:
        logger.info('no GSMs fit the current r_free_to_use ({0}), '
                    'no transferring will happen'.format(
                        pretty_usage(r_free_to_use)))
        remove_locker(locker)
        return

    logger.info('GSMs to transfer:')
    for gsm in gsms_to_transfer:
        logger.info('\t{0}'.format(gsm))

    # create transfer script
    transfer_script = os.path.join(
        transfer_scripts_dir, '{0}.sh'.format(job_name))
    # locker is used to prevent the next session of transfer from starting
    # before the current session of transfer finishes
    write(transfer_script, options.rsync_template,
          job_name=job_name,
          username=r_username,
          hostname=r_host,
          gsms_to_transfer=gsms_to_transfer,
          local_top_outdir=l_top_outdir,
          remote_top_outdir=r_top_outdir)

    os.chmod(transfer_script, stat.S_IRUSR | stat.S_IWUSR| stat.S_IXUSR)
    rcode = execute_log_stdout_stderr(transfer_script)

    if rcode == 0:
        append_transfer_record(gsms_to_transfer, gsms_transfer_record)
        remove_locker(locker)


if __name__ == "__main__":
    sys.stdout.flush()          #flush print outputs to screen

    # global variables: options, config
    options = parse_args_for_rsem_transfer()
    try:
        with open(options.config_file) as inf:
            config = yaml.load(inf.read())
    except IOError, _:
        print 'configuration file: {0} not found'.format(options.config_file)
        sys.exit(1)

    logging.config.fileConfig(os.path.join(
        os.path.dirname(__file__), 'rsem_cron_transfer.logging.config'))

    logger = logging.getLogger('rsem_cron_transfer')
    main()