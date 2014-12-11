#!/usr/bin/env python
# -*- coding: utf-8 -*

"""
This script parse soft files, download sra, convert sra to fastq and run rsem
on them, either on local system or on remote cluster.
"""

import os
import re
import logging.config

import urlparse
import yaml

import ruffus as R

from jinja2 import Environment, FileSystemLoader

from rsem_pipeline.utils import misc
from rsem_pipeline.utils import pre_pipeline_run as PPR
from rsem_pipeline.utils.download import gen_orig_params
from rsem_pipeline.utils.rsem import gen_fastq_gz_input
from rsem_pipeline.conf.settings import RP_RUN_LOGGING_CONFIG
from rsem_pipeline.parsers.args_parser import parse_args_for_rsem_pipeline

PATH_RE = r'(.*)/(?P<GSE>GSE\d+)/(?P<species>\S+)/(?P<GSM>GSM\d+)'

# because of ruffus, have to use some global variables
# global variables: options, config, samples, env, logger, logger_mutex
# minimize the number of global variables as much as possible
options = parse_args_for_rsem_pipeline()
with open(options.config_file) as inf:
    config = yaml.load(inf.read())

if not os.path.exists('log'):
    os.mkdir('log')
logging.config.fileConfig(RP_RUN_LOGGING_CONFIG)

samples = PPR.gen_samples_from_soft_and_isamp(
    options.soft_files, options.isamp, config)

env = Environment(loader=FileSystemLoader([
    # the standard templates directory
    os.path.join(os.path.dirname(__file__), 'templates'),
    # the current working directory
    os.getcwd()]))

logger, logger_mutex = R.proxy_logger.make_shared_logger_and_proxy(
    R.proxy_logger.setup_std_shared_logger,
    "rsem_pipeline",
    {"config_file": os.path.join(RP_RUN_LOGGING_CONFIG)})

LOCKER_PATTERN = os.path.join(config['LOCAL_TOP_OUTDIR'], '.rp-run')

@misc.lockit(LOCKER_PATTERN)
def prepare_pipeline_run():
    logger.info('Preparing sample outdirs')
    PPR.init_sample_outdirs(samples, config['LOCAL_TOP_OUTDIR'])
    logger.info('Fetching sras info')
    PPR.fetch_sras_info(samples, options.recreate_sras_info)
    logger.info('Selecting samples to process based their usages, '
                'available disk size and parameters specified '

                'in {0}'.format(options.config_file))
    return PPR.select_samples_to_process(samples, config, options)
samples = prepare_pipeline_run()

##################################end of main##################################

# def execute_mutex(cmd, msg_id='', flag_file=None, debug=False):
#     """
#     :param msg_id: id for identifying a message, this prevents parallel
#     processing
#     """
#     with logger_mutex:
#         returncode = misc.execute(cmd, msg_id, flag_file, debug)
#     return returncode


def originate_params():
    """
    Generate a list of sras to download for each sample

    This function gets called twice, once before entering the queue, once after
    """
    num_samples = len(samples)
    logger.info(
        'preparing originate_params for {0} samples'.format(num_samples))
    orig_params_sets = gen_orig_params(samples)
    logger.info(
        '{0} sets of orig_params generated'.format(len(orig_params_sets)))
    for _ in orig_params_sets:
        yield _


@R.files(originate_params)
def download(_, outputs, sample):
    """inputs (_) is None"""
    msg_id = misc.gen_sample_msg_id(sample)
    # e.g. sra
    # test_data_downloaded_for_genesis/rsem_output/human/GSE24455/GSM602557/SRX029242/SRR070177/SRR070177.sra
    sra, flag_file = outputs    # the others are sra files
    sra_outdir = os.path.dirname(sra)
    if not os.path.exists(sra_outdir):
        os.makedirs(sra_outdir)
    # e.g. url_path:
    # /sra/sra-instant/reads/ByExp/sra/SRX/SRX029/SRX029242
    url_path = urlparse.urlparse(sample.url).path
    sra_url_path = os.path.join(url_path, *sra.split('/')[-2:])

    # cmd template looks like this:
    # /home/kmnip/bin/ascp 
    # -i /home/kmnip/.aspera/connect/etc/asperaweb_id_dsa.putty 
    # --ignore-host-key 
    # -QT 
    # -L {log_dir}
    # -k2 
    # -l 300m 
    # anonftp@ftp-trace.ncbi.nlm.nih.gov:{url_path} {output_dir}
    cmd = config['CMD_ASCP'].format(
        log_dir=sra_outdir, url_path=sra_url_path, output_dir=sra_outdir)
    returncode = misc.execute(cmd, msg_id, flag_file, options.debug)
    if returncode != 0 or returncode is None:
        # try wget
        # cmd template looks like this:
        # wget ftp://ftp-trace.ncbi.nlm.nih.gov{url_path} -P {output_dir} -N
        cmd = config['CMD_WGET'].format(
            url_path=sra_url_path, output_dir=sra_outdir)
        misc.execute(cmd, msg_id, flag_file, options.debug)

               
@R.subdivide(
    download,
    R.formatter(r'{0}/(?P<RX>[SED]RX\d+)/(?P<RR>[SED]RR\d+)/(.*)\.sra'.format(PATH_RE)),
    ['{subpath[0][2]}/{RR[0]}_[12].fastq.gz',
     '{subpath[0][2]}/{RR[0]}.sra.sra2fastq.COMPLETE'])
def sra2fastq(inputs, outputs):
    """for meaning of [SED]RR, see
    http://www.ncbi.nlm.nih.gov/books/NBK56913/#search.the_entrez_sra_search_response_pa

    S =NCBI-SRA, E = EMBL-SRA, D = DDBJ-SRA
    SRR: SRA run accession
    ERR: ERA run accession
    DRR: DRA run accession
    """
    sra, _ = inputs             # ignore the flag file from previous task
    print inputs
    flag_file = outputs[-1]
    outdir = os.path.dirname(os.path.dirname(os.path.dirname(sra)))
    cmd = config['CMD_FASTQ_DUMP'].format(output_dir=outdir, accession=sra)
    misc.execute_log_stdout_stderr(cmd, flag_file=flag_file, debug=options.debug)


@R.collate(
    sra2fastq,
    R.formatter(PATH_RE),
    '{subpath[0][0]}/0_submit.sh')
def gen_qsub_script(inputs, outputs):
    """generate qsub script, usually named 0_submit.sh"""
    inputs = [_ for _ in inputs if not _.endswith('.sra2fastq.COMPLETE')]
    outdir = os.path.dirname(inputs[0])

    # only need the basename since the 0_submit.sh will be executed in the
    # GSM dir
    fastq_gz_input = gen_fastq_gz_input(
        [os.path.basename(_) for _ in inputs])
    res = re.search(PATH_RE, outdir)
    gse = res.group('GSE')
    species = res.group('species')
    gsm = res.group('GSM')
    reference_name = config['REMOTE_REFERENCE_NAMES'][species]
    sample_name = '{gsm}'.format(gsm=gsm)
    n_jobs = misc.decide_num_jobs(outdir, options.j_rsem)

    qsub_script = os.path.join(outdir, '0_submit.sh')
    template = env.get_template(options.qsub_template)
    with open(qsub_script, 'wb') as opf:
        content = template.render(**locals())
        opf.write(content)
        logger.info('templated {0}'.format(qsub_script))


@R.collate(
    sra2fastq,
    # the commented R.formatter line is for reference only, use the next one
    # because thus ruffus can guarantee that all the required
    # sra2fastq.COMPLETE files do exist before starting rsem, this is
    # inconvenient when it comes to multiple samples because a single missing
    # sra2fastq.COMPLETE will make the analysis for all samples crash, but
    # generally you run one sample at a time when it comes to sra2fastq and
    # rsem, so it should be fine most of the time

    R.formatter(PATH_RE),
    ['{subpath[0][0]}/{GSM[0]}.genes.results',
     '{subpath[0][0]}/{GSM[0]}.isoforms.results',
     '{subpath[0][0]}/{GSM[0]}.stat/{GSM[0]}.cnt',
     '{subpath[0][0]}/{GSM[0]}.stat/{GSM[0]}.model',
     '{subpath[0][0]}/{GSM[0]}.stat/{GSM[0]}.theta',
     '{subpath[0][0]}/align.stats',
     '{subpath[0][0]}/rsem.log',
     '{subpath[0][0]}/rsem.COMPLETE'])

# example of outputs:
# GSE42735/
# `-- homo_sapiens
#     |-- GSM1048945
#     |   |-- GSM1048945.genes.results
#     |   |-- GSM1048945.isoforms.results
#     |   |-- GSM1048945.stat
#     |   |   |-- GSM1048945.cnt
#     |   |   |-- GSM1048945.model
#     |   |   `-- GSM1048945.theta
#     |   |-- align.stats
#     |   |-- rsem.COMPLETE
#     |   `-- rsem.log
def rsem(inputs, outputs):
    """
    :params inputs: a tuple of 1 or 2 fastq.gz files, e.g.
    ('/path/to/rsem_output/homo_sapiens/GSE50599/GSM1224499/SRR968078_1.fastq.gz',
     '/path/to/rsem_output/homo_sapiens/GSE50599/GSM1224499/SRR968078_2.fastq.gz')
    """
    inputs = [_ for _ in inputs if not _.endswith('.sra2fastq.COMPLETE')]
    # this is equivalent to the sample.outdir or GSM dir
    outdir = os.path.dirname(inputs[0])

    # the names of parameters are the same as that in gen_qsub_script, but
    # their values are more or less different, so better keep them separate
    fastq_gz_input = gen_fastq_gz_input(inputs)
    res = re.search(PATH_RE, outdir)
    gse = res.group('GSE')
    species = res.group('species')
    gsm = res.group('GSM')
    reference_name = config['LOCAL_REFERENCE_NAMES'][species]
    sample_name = '{outdir}/{gsm}'.format(**locals())
    n_jobs = misc.decide_num_jobs(outdir, options.j_rsem)

    flag_file = outputs[-1]
    cmd = config['CMD_RSEM'].format(
        n_jobs=n_jobs,
        fastq_gz_input=fastq_gz_input,
        reference_name=reference_name,
        sample_name=sample_name,
        output_dir=outdir)
    misc.execute_log_stdout_stderr(cmd, flag_file=flag_file, debug=options.debug)


def main():
    if samples is None:
        pass                    # meaning it's locked.
    elif samples:
        logger.info('GSMs to process:')
        for k, gsm in enumerate(samples):
            logger.info('\t{0:3d} {1:30s} {2}'.format(k+1, gsm, gsm.outdir))
        if 'gen_qsub_script' in options.target_tasks:
            if not options.qsub_template:
                raise IOError('-t/--qsub_template required when running gen_qsub_script')
        pipeline_run = misc.lockit(LOCKER_PATTERN)(R.pipeline_run)
        pipeline_run(
            logger=logger,
            target_tasks=options.target_tasks,
            forcedtorun_tasks=options.forced_tasks,
            multiprocess=options.jobs,
            verbose=options.verbose,
            touch_files_only=options.touch_files_only,
            # history_file=os.path.join('log', '.{0}.sqlite'.format(
            #     '_'.join([_.name for _ in sorted(samples, key=lambda x: x.name)])))
        )
    else:                       # meaning if samples != []
        logger.info('Cannot find a GSM that fits the disk usage rule')


if __name__ == "__main__":
    main()