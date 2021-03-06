import unittest
import logging
import logging.config

import mock

from rsempipeline.parsers import soft_parser
from rsempipeline.utils.objs import Series, Sample

logger = logging.getLogger('rsempipeline.parsers.soft_parser')
logger.addHandler(logging.StreamHandler())

import settings


class SoftParserTestCase(unittest.TestCase):
    def test_get_series_name_from_soft_file(self):
        name = soft_parser.get_series_name_from('GSE31555_family.soft.subset')
        self.assertEqual(name, 'GSE31555')

    def test_get_series_name_from_soft_file_with_invalid_file_name(self):
        self.assertRaises(ValueError, soft_parser.get_series_name_from,
                          'some.soft.subset')

    def test_add_with_current_sample_being_None(self):
        series = Series('GSE123456', 'GSE123456_family.soft.subset')
        self.assertEqual(soft_parser.add(None, series, 1), 1)

    @mock.patch.object(soft_parser.Sample, 'is_info_complete')
    def test_add(self, mock_is_info_complete):
        series = Series('GSE123456', 'GSE123456_family.soft.subset')
        current_sample = Sample('GSM1', series)
        mock_is_info_complete.return_value = True
        self.assertEqual(soft_parser.add(current_sample, series, 1), 2)
        mock_is_info_complete.return_value = False
        self.assertEqual(soft_parser.add(current_sample, series, 1), 1)

    def test_parse(self):
        m = mock.mock_open()
        with mock.patch('rsempipeline.parsers.soft_parser.open', m):
            m.return_value.__iter__.return_value = settings.GSE43770_FAMILY_SOFT_SUBSET_CONTENT.splitlines()
            series = soft_parser.parse('GSE43770_family.soft.subset',
                              ['Homo sapiens', 'Mus musculus'])
            m.assert_called_once_with('GSE43770_family.soft.subset', 'rb')
            self.assertEqual(series.name, 'GSE43770')
            self.assertEqual([__.name for __ in series.passed_samples],
                             ['GSM1070765', 'GSM1070766'])
            self.assertEqual([__.index for __ in series.passed_samples],
                             [1, 2])
            self.assertEqual([__.name for __ in series.samples],
                             ['GSM1070765', 'GSM1070766', 'GSM1070767'])
            self.assertEqual([__.index for __ in series.samples],
                             [1, 2, 0]) # 0 mean unindexed

    def test_parse_invalid_GSE44770(self):
        m = mock.mock_open()
        with mock.patch('rsempipeline.parsers.soft_parser.open', m):
            m.return_value.__iter__.return_value = settings.INVALID_GSE43770_FAMILY_SOFT_SUBSET_CONTENT.splitlines()
            self.assertRaisesRegexp(
                ValueError, 'GSE00000 \(passed samples\: 0\/0\) != GSE43770', soft_parser.parse,
                'GSE43770_family.soft.subset', ['Homo sapiens', 'Mus musculus'])


# class SOFTDownloaderTestCase(unittest.TestCase):
#     gse1 = 'GSE45284'           # a real one
#     gse2 = 'GSE12345678'        # a fake one

#     # because internet connection takes a long time
#     @mock.patch('rsempipeline.preprocess.get_soft.FTP')
#     def setUp(self, mock_FTP):
#         self.der = get_soft.SOFTDownloader()

#     def test_init(self):
#         self.der.ftp_handler.login.assert_called_once_with()

#     def test_base(self):
#         self.assertEqual(self.der.base, 'ftp://ftp.ncbi.nlm.nih.gov')
        
#     def test_get_soft_gz_basename(self):
#         self.assertEqual(self.der.get_soft_gz_basename(self.gse1),
#                          'GSE45284_family.soft.gz')

#     def test_get_gse_mask(self):
#         self.assertEqual(self.der.get_gse_mask(self.gse1), 'GSE45nnn')
#         self.assertEqual(self.der.get_gse_mask(self.gse2), 'GSE12345nnn')

#     def test_get_remote_path(self):
#         self.assertEqual(self.der.get_remote_path(self.gse1),
#                          '/geo/series/GSE45nnn/GSE45284/soft')

#     def test_get_url(self):
#         self.assertEqual(self.der.get_url(self.gse1),
#                          'ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE45nnn/GSE45284/soft/GSE45284_family.soft.gz')

#     def test_retrieve(self):
#         mock_open = mock.mock_open()
#         with mock.patch('rsempipeline.preprocess.get_soft.open',
#                         mock_open, create=True):
#             self.der.retrieve('the_path', 'the.soft.gz', 'the_output')
#         mock_open.assert_called_once_with('the_output', 'wb')
#         self.der.ftp_handler.cwd.assert_called_with('the_path')
#         fd = mock_open.return_value.__enter__.return_value
#         cmd = 'RETR the.soft.gz'
#         self.der.ftp_handler.retrbinary.assert_called_with(cmd, fd.write)

#     @mock.patch('rsempipeline.preprocess.get_soft.open', create=True)
#     def test_retrieve2(self, mock_open):
#         """supposed to have the same testing purpose as self.test_retrieve, but using
#         patch as a decorator instead of context manager
#         """
#         # mock_open.return_value = mock.MagicMock(spec=file)
#         self.der.retrieve('the_path', 'the.soft.gz', 'the_output')
#         mock_open.assert_called_once_with('the_output', 'wb')
#         self.der.ftp_handler.cwd.assert_called_with('the_path')
#         cmd = 'RETR the.soft.gz'
#         fd = mock_open.return_value.__enter__.return_value
#         self.der.ftp_handler.retrbinary.assert_called_with(cmd, fd.write)

#     @mock.patch.object(get_soft.SOFTDownloader, 'retrieve')
#     @log_capture()
#     def test_download_soft_gz(self, mock_retrieve, L):
#         res = self.der.download_soft_gz(self.gse1, 'any_outdir')
#         self.assertEqual(res, 'any_outdir/GSE45284_family.soft.gz')
#         args = ('/geo/series/GSE45nnn/GSE45284/soft', 'GSE45284_family.soft.gz', res)
#         mock_retrieve.assert_called_once_with(*args)
#         L.check(('rsempipeline.preprocess.get_soft', 'INFO',
#                  'downloading GSE45284_family.soft.gz from ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE45nnn/GSE45284/soft/GSE45284_family.soft.gz to any_outdir/GSE45284_family.soft.gz'))

#         # when retrieve raises Exception
#         mock_retrieve.side_effect = Exception()
#         res = self.der.download_soft_gz(self.gse1, 'any_outdir')
#         self.assertIsNone(res)
#         mock_retrieve.assert_called_with(*args)
#         self.assertEqual(mock_retrieve.call_count, 2)
#         self.assertIn('error when downloading', str(L))

#     def test_get_soft_subset(self):
#         self.assertEqual(self.der.get_soft_subset(self.gse1, 'any_outdir'),
#                          'any_outdir/GSE45284_family.soft.subset')

#     @mock.patch.object(get_soft.gzip, 'open')
#     @mock.patch('rsempipeline.preprocess.get_soft.os.path')
#     @log_capture()
#     def test_gunzip_and_extract_soft(self, mock_path, mock_gzip_open, L):
#         soft_gz = 'any_dir/the.soft.gz'
#         soft_subset = 'any_dir/the.soft.subset'
#         mock_open = mock.mock_open()
#         with mock.patch('rsempipeline.preprocess.get_soft.open', mock_open, create=True):
#             self.der.gunzip_and_extract_soft(soft_gz, soft_subset)
#         mock_open.assert_called_once_with(soft_subset, 'wb')
#         mock_gzip_open.assert_called_once_with(soft_gz, 'rb')
#         L.check(('rsempipeline.preprocess.get_soft', 'INFO',
#                  'gunziping and extracting from any_dir/the.soft.gz to any_dir/the.soft.subset'))

#     # def test_gunzip_and_extract_soft_with_real_gz_files(self):
#     #     # to be written
#     #     pass

#     @mock.patch.object(get_soft.os.path, 'exists')
#     @mock.patch.object(get_soft.SOFTDownloader, 'download_soft_gz', autospec=True)
#     @log_capture()
#     def test_gen_soft_with_already_existing_soft_subset(
#             self, mock_download_soft_gz, mock_exists, L):
#         mock_exists.return_value = True
#         self.der.gen_soft(self.gse1, 'any_outdir')
#         self.assertFalse(mock_download_soft_gz.called)
#         L.check(('rsempipeline.preprocess.get_soft', 'INFO',
#                  'any_outdir/GSE45284_family.soft.subset has already existed'))


#     @mock.patch.object(get_soft.os, 'remove')
#     @mock.patch.object(get_soft.os.path, 'exists')
#     @mock.patch.object(get_soft.SOFTDownloader, 'gunzip_and_extract_soft')
#     @mock.patch.object(get_soft.SOFTDownloader, 'download_soft_gz')
#     @log_capture()
#     def test_gen_soft_without_existing_soft_subset_with_successful_download_soft_gz(
#             self,
#             mock_download_soft_gz,
#             mock_gunzip_and_extract_soft,
#             mock_exists,
#             mock_remove,
#             L):
#         gz = 'any_outdir/GSE45284_family.soft.gz'
#         subset = 'any_outdir/GSE45284_family.soft.subset'
#         mock_exists.return_value = False
#         mock_download_soft_gz.return_value = gz
#         res = self.der.gen_soft(self.gse1, 'any_outdir')
#         self.assertEqual(res, subset)
#         mock_download_soft_gz.assert_called_with(self.gse1, 'any_outdir')
#         mock_gunzip_and_extract_soft.assert_called_with(gz, subset)
#         mock_remove.assert_called_with(gz)
#         L.check(('rsempipeline.preprocess.get_soft', 'INFO',
#                  'removing any_outdir/GSE45284_family.soft.gz'))


#     @mock.patch.object(get_soft.os, 'remove')
#     @mock.patch.object(get_soft.os.path, 'exists')
#     @mock.patch.object(get_soft.SOFTDownloader, 'gunzip_and_extract_soft')
#     @mock.patch.object(get_soft.SOFTDownloader, 'download_soft_gz')
#     @log_capture()
#     def test_gen_soft_without_existing_soft_subset_with_unsuccessful_download_soft_gz(
#             self,
#             mock_download_soft_gz,
#             mock_gunzip_and_extract_soft,
#             mock_exists,
#             mock_remove,
#             L):
#         mock_exists.return_value = False
#         mock_download_soft_gz.return_value = None
#         res = self.der.gen_soft(self.gse1, 'any_outdir')
#         self.assertIsNone(res)
#         mock_download_soft_gz.assert_called_with(self.gse1, 'any_outdir')
#         self.assertFalse(mock_gunzip_and_extract_soft.called)
#         self.assertFalse(mock_remove.called)
