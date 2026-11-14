import os
import sys
import unittest
from unittest.mock import patch

import deepai_proxy_core
import iniciar


class PortaFixaTests(unittest.TestCase):
    def test_default_and_custom_port(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(iniciar.porta_proxy(), '8791')
        with patch.dict(os.environ, {'DEEP_PORT': '9999'}, clear=True):
            self.assertEqual(iniciar.porta_proxy(), '9999')
        for valor in ('0', '65536', 'abc'):
            with patch.dict(os.environ, {'DEEP_PORT': valor}, clear=True):
                self.assertEqual(iniciar.porta_proxy(), '8791')

    def test_gateway_default_matches_launcher(self):
        with patch.dict(os.environ, {'DEEPAI_PROXY_INTERNAL': '1'}), patch.object(sys, 'argv', ['proxy.py']), patch.object(deepai_proxy_core, 'serve') as serve:
            deepai_proxy_core.main()
        serve.assert_called_once_with(iniciar.PORTA_PADRAO)


if __name__ == '__main__': unittest.main()
