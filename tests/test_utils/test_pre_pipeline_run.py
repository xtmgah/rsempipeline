import unittest
import mock

from testfixtures import log_capture

from rsempipeline.utils.objs import Series, Sample
from rsempipeline.utils import pre_pipeline_run as ppr


SRA_INFO_YAML_SINGLE_SRA = """- SRX685892/SRR1557065/SRR1557065.sra:
    readable_size: 2.4 GB
    size: 2546696608"""

PARSED_SRA_INFO_YAML_SINGLE_SRA = [
    {
        'SRX685892/SRR1557065/SRR1557065.sra': {
            'readable_size': '2.4 GB',
            'size': 2546696608
        }
    }
]


class PrePipelineRunTestCase(unittest.TestCase):
    @mock.patch('rsempipeline.utils.pre_pipeline_run.FTP', autospec=True)
    def test_get_ftp_handler(self, mock_FTP):
        sample_url = 'ftp://ftp-trace.ncbi.nlm.nih.gov/sra/sra-instant/reads/ByExp/sra/SRX/SRX029/SRX029242'
        ppr.get_ftp_handler(sample_url)
        mock_FTP.assert_called_once_with('ftp-trace.ncbi.nlm.nih.gov')
        self.assertEqual(mock_FTP.return_value.login.call_count, 1)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.write', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_ftp_handler', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.fetch_sras_info_per', autospec=True)
    def test_fetch_sras_info_from_scratch(self, mock_fetch, mock_get_ftp_handler, mock_write, mock_os):
        mock_os.path.exists.return_value = False
        mock_fetch.return_value = PARSED_SRA_INFO_YAML_SINGLE_SRA
        ppr.fetch_sras_info(samples=[mock.Mock(), mock.Mock()],
                            flag_recreate_sras_info=False)
        self.assertEqual(mock_get_ftp_handler.call_count, 1)
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_write.call_count, 2)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.write', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_ftp_handler', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.fetch_sras_info_per', autospec=True)
    def test_fetch_sras_info_with_already_existent_yml(self, mock_fetch, mock_get_ftp_handler, mock_write, mock_os):
        mock_os.path.exists.return_value = True
        mock_fetch.return_value = PARSED_SRA_INFO_YAML_SINGLE_SRA
        ppr.fetch_sras_info(samples=[mock.Mock(), mock.Mock()],
                            flag_recreate_sras_info=False)
        self.assertEqual(mock_get_ftp_handler.call_count, 1)
        self.assertEqual(mock_fetch.call_count, 0)
        self.assertEqual(mock_write.call_count, 0)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.write', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_ftp_handler', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.fetch_sras_info_per', autospec=True)
    def test_fetch_sras_info_recreate(self, mock_fetch, mock_get_ftp_handler, mock_write, mock_os):
        mock_os.path.exists.return_value = True
        mock_fetch.return_value = PARSED_SRA_INFO_YAML_SINGLE_SRA
        ppr.fetch_sras_info(samples=[mock.Mock(), mock.Mock()],
                            flag_recreate_sras_info=True)
        self.assertEqual(mock_get_ftp_handler.call_count, 1)
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_write.call_count, 2)

    def test_fetch_sras_info_per(self):
        mock_ftp_handler = mock.Mock()
        mock_ftp_handler.nlst.side_effect = [['SRX0/SRR0'], ['SRX0/SRR0/SRR0.sra']]
        mock_ftp_handler.size.return_value = 1024 ** 2
        fake_sample_url = 'ftp://ftp-trace.ncbi.nlm.nih.gov/sra/sra-instant/reads/ByExp/sra/SRX/SRX029/SRX029242'
        res = ppr.fetch_sras_info_per(fake_sample_url, mock_ftp_handler)
        mock_ftp_handler.cwd.assert_called_once_with('/sra/sra-instant/reads/ByExp/sra/SRX/SRX029')
        self.assertEqual(mock_ftp_handler.nlst.call_args_list,
                         [mock.call('SRX029242'), mock.call('SRX0/SRR0')])
        mock_ftp_handler.sendcmd.assert_called_once_with('TYPE i')
        mock_ftp_handler.size.assert_called_once_with('SRX0/SRR0/SRR0.sra')
        self.assertEqual(res, [
            {
                'SRX0/SRR0/SRR0.sra': {
                    'readable_size': '1.0 MB',
                    'size': 1048576
                }
            }
        ])

    def gen_fake_isamp(self):
        return {
            'GSE0': ['GSM10', 'GSM20'],
            'GSE1': ['GSM11']
        }

    def test_calc_num_isamp(self):
        fake_isamp = self.gen_fake_isamp()
        self.assertEqual(ppr.calc_num_isamp(fake_isamp), 3)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.sanity_check', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.analyze_one', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_isamp', autospec=True)
    def test_gen_all_samples_from_soft_and_isamp(
            self, mock_get_isamp, mock_analyze_one, mock_sanity_check):
        mock_sanity_check.return_value = True
        mock_get_isamp.return_value = self.gen_fake_isamp()
        fake_series = Series('GSE0')
        sample_list = [Sample('GSM10', fake_series), Sample('GSM20', fake_series)]
        for __ in sample_list :
            __.organism = 'Homo Sapiens'
            fake_series.add_passed_sample(__)
        mock_analyze_one.return_value = sample_list

        self.assertEqual(ppr.gen_all_samples_from_soft_and_isamp(
            ['soft1'], 'isamp_file_or_str', {'INTERESTED_ORGANISMS': ['Homo Sapiens']}),
            sample_list)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.parse', autospec=True)
    @log_capture()
    def test_analyze_one(self, mock_parse, L):
        fake_isamp = self.gen_fake_isamp()
        fake_series = Series('GSE0')
        sample_list = [Sample('GSM10', fake_series)]
        for __ in sample_list :
            __.organism = 'Homo Sapiens'
            fake_series.add_passed_sample(__)
        mock_parse.return_value = fake_series
        self.assertEqual(ppr.analyze_one('GSE0_family.soft.subset', fake_isamp, ['Homo sapiens']),
                         sample_list)
        L.check(('rsempipeline.utils.pre_pipeline_run', 'ERROR',
                 'Discrepancy for GSE0: 1 GSMs in soft, 2 GSMs in isamp, and only 1 left after intersection.'),)


    def test_analyze_one_invalid_filename(self):
        self.assertIsNone(
            ppr.analyze_one('invalid_soft_filename', 'some_fake_isamp', ['']))

    @mock.patch('rsempipeline.utils.pre_pipeline_run.parse', autospec=True)
    @log_capture()
    def test_analyze_one_soft_series_name_not_in_isamp_series_names_list(self, mock_parse, L):
        fake_isamp = self.gen_fake_isamp()
        fake_series = Series('GSE9999')
        mock_parse.return_value = fake_series
        self.assertIsNone(ppr.analyze_one('GSE9999_family.soft.subset', fake_isamp, []))

    def test_intersect(self):
        isamp = self.gen_fake_isamp()
        series = Series('GSE0')
        sample_list = [Sample('GSM10', series), Sample('GSM20', series)]
        for __ in sample_list :
            series.add_passed_sample(__)
        self.assertEqual(ppr.intersect(series, isamp), sample_list)

    @log_capture()
    def test_intersect_with_discrenpacy(self, L):
        isamp = self.gen_fake_isamp()
        series = Series('GSE0')
        sample_list = [Sample('GSM10', series)]
        for __ in sample_list :
            series.add_passed_sample(__)
        self.assertEqual(ppr.intersect(series, isamp), sample_list)
        L.check(('rsempipeline.utils.pre_pipeline_run', 'ERROR',
                 'Discrepancy for GSE0: 1 GSMs in soft, 2 GSMs in isamp, and only 1 left after intersection.'),)

    def test_filename_check(self):
        self.assertTrue(ppr.filename_check('GSE63311_family.soft.subset'))

    @log_capture()
    def test_filename_check_with_invalid_filename(self, L):
        self.assertFalse(ppr.filename_check('customized_soft_filename'))
        L.check(('rsempipeline.utils.pre_pipeline_run', 'ERROR',
                 'invalid soft file because of no GSE information found in its filename: customized_soft_filename'),)

    def test_sanity_check(self):
        self.assertRaises(ValueError, ppr.sanity_check, 1000, 1010)
        self.assertIsNone(ppr.sanity_check(1000, 1000))

    def test_get_rsem_outdir(self):
        self.assertEqual(ppr.get_rsem_outdir('some_outdir'), 'some_outdir/rsem_output')

    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    def test_init_sample_outdirs(self, mock_os):
        mock_os.path.exists.return_value = False
        fake_samples = [mock.Mock(), mock.Mock()]
        ppr.init_sample_outdirs(fake_samples, 'some_top_outdir')
        self.assertEqual(mock_os.makedirs.call_count, 2)

    # @mock.patch('rsempipeline.utils.pre_pipeline_run.disk_free')
    # @mock.patch('rsempipeline.utils.pre_pipeline_run.disk_used')
    # @mock.patch('rsempipeline.utils.pre_pipeline_run.find_gsms_to_process')
    # @mock.patch('rsempipeline.utils.pre_pipeline_run.calc_free_space_to_use')
    # def test_select_samples_to_process(self, mock_calc, mock_find, mock_du, mock_df):
    #     """mainly to test the kind of configuration parameters needed"""
    #     mock_du.return_value = 1
    #     mock_df.return_value = 1

    #     mock_samples = [mock.Mock()]
    #     mock_config = {
    #         'LOCAL_TOP_OUTDIR': '',
    #         'LOCAL_CMD_DF': '',
    #         'LOCAL_MAX_USAGE': '1 GB',
    #         'LOCAL_MIN_FREE': '500 MB'
    #     }
    #     mock_options = mock.Mock()
    #     mock_options.ignore_disk_usage_rule = False
    #     ppr.select_samples_to_process(mock_samples, mock_config, mock_options)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_processed', autospec=True)
    def test_select_gsms_to_process_all_processed(self, mock_is_processed):
        mock_is_processed.return_value = True
        samples = [mock.Mock(), mock.Mock()]
        self.assertEqual(ppr.select_gsms_to_process(samples, 1024 ** 3, False), [])

    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_processed', autospec=True)
    def test_select_gsms_to_process_ignore_disk_usage(self, mock_is_processed):
        mock_is_processed.return_value = False
        samples = [mock.Mock(), mock.Mock()]
        self.assertEqual(ppr.select_gsms_to_process(samples, 1024 ** 3, True), samples)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.estimate_sra2fastq_usage', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_processed', autospec=True)
    def test_select_gsms_to_process_fit_disk_usage(self, mock_is_processed, mock_estimate_sra2fastq_usage):
        mock_is_processed.return_value = False
        mock_estimate_sra2fastq_usage.return_value = 513
        samples = [mock.Mock(), mock.Mock()]
        self.assertEqual(ppr.select_gsms_to_process(samples, 1024, False), [samples[0]])

    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    def test_is_gen_qsub_script_complete(self, mock_os):
        mock_os.path.exists.return_value = True
        self.assertTrue(ppr.is_gen_qsub_script_complete('some_gsm_dir'))
        mock_os.path.exists.return_value = False
        self.assertFalse(ppr.is_gen_qsub_script_complete('some_gsm_dir'))
        
    @mock.patch('rsempipeline.utils.pre_pipeline_run.os', autospec=True)
    def test_get_sras_info(self, _):
        with mock.patch('rsempipeline.utils.pre_pipeline_run.open',
                        mock.mock_open(read_data=SRA_INFO_YAML_SINGLE_SRA)):
            res = ppr.get_sras_info('some_dir')
        self.assertEqual(res, PARSED_SRA_INFO_YAML_SINGLE_SRA)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_sras_info', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.SRA2FASTQ_SIZE_RATIO', autospec=True)
    def test_estimate_sra2fastq_usage(self, mock_ratio, mock_get_sras_info):
        mock_ratio = 2
        mock_get_sras_info.return_value = PARSED_SRA_INFO_YAML_SINGLE_SRA
        self.assertEqual(ppr.estimate_sra2fastq_usage('some_gsm_dir'), 2546696608 * mock_ratio)

    @mock.patch('rsempipeline.utils.pre_pipeline_run.get_sras_info', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_gen_qsub_script_complete', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_sra2fastq_complete', autospec=True)
    @mock.patch('rsempipeline.utils.pre_pipeline_run.is_download_complete', autospec=True)
    def test_is_process(self, mock_g, mock_s, mock_d, mock_get_sras_info):
        mock_get_sras_info
        mock_d.return_value = False
        self.assertFalse(ppr.is_processed('some_gsm_dir'))
        mock_d.return_value = True
        mock_s.return_value = False
        self.assertFalse(ppr.is_processed('some_gsm_dir'))
        mock_d.return_value = True
        mock_s.return_value = True
        mock_g.return_value = False
        self.assertFalse(ppr.is_processed('some_gsm_dir'))
        mock_d.return_value = True
        mock_s.return_value = True
        mock_g.return_value = True
        self.assertTrue(ppr.is_processed('some_gsm_dir'))
