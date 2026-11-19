import hashlib,re
from tests.support import SourceTest

class AdvisoryTest(SourceTest):
    def test_revision_digest_exclusion_and_assets(self):
        page=self.request('/vendor/advisory?id=ADV-A')[1].decode();sha=re.search('data-sha256="([a-f0-9]+)"',page)[1]
        self.assertEqual(hashlib.sha256(self.request('/machine?id=ADV-A&revision=1')[1]).hexdigest(),sha)
        self.control('exclude');self.assertEqual(self.get('/machine?id=ADV-A&revision=2')['products'][0]['excluded'],['1.3.2']);self.assertIn('修订 1',self.request('/mirror/advisory?id=ADV-A')[1].decode())
        self.control('mismatch');self.assertNotEqual(hashlib.sha256(self.request('/machine?id=ADV-A&revision=1')[1]).hexdigest(),sha)
        old=self.get('/vendor/index');self.control('index_shift');self.assertNotEqual(self.get('/vendor/index')['index_revision'],old['index_revision'])
        self.control('asset_upgrade');self.assertEqual(self.get('/assets')['assets'][0]['version'],'1.4.0');self.control('throttle');self.assertEqual(self.request('/vendor/index')[0],429)
