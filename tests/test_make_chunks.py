import pytest

from piidigger.globalfuncs import makeChunks

#The first test breaks up the string on a word boundary
#The second test is just a bit too long (next word boundary exceeds the chunk size), but still passes
#The third test is short enough that only one chunk is produced.
#The fourth test uses a single, too-long string that exceeds the chunk size, so the string is broken right at the chunksize.

@pytest.mark.utils
@pytest.mark.parametrize('content, expected_result', [
                                ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Vitae proin sagittis nisl rhoncus mattis rhoncus urna neque. Neque viverra justo nec ultrices dui sapien. Turpis cursus in hac habitasse. Eu facilisis sed odio morbi quis. Commodo ullamcorper a lacus vestibulum. In metus vulputate eu scelerisque felis imperdiet. Orci a scelerisque purus semper eget duis at. Tincidunt augue interdum velit euismod. Dignissim enim sit amet venenatis urna cursus eget. Consequat mauris nunc congue nisi vitae suscipit tellus. Cursus euismod quis viverra nibh. Platea dictumst vestibulum rhoncus est pellentesque elit ullamcorper dignissim.', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Vitae proin sagittis nisl rhoncus mattis rhoncus urna neque. Neque viverra justo nec ultrices dui sapien. Turpis cursus in hac habitasse. Eu facilisis sed odio morbi quis. Commodo ullamcorper a lacus vestibulum. In metus vulputate eu scelerisque felis imperdiet. Orci a scelerisque purus semper eget duis at. Tincidunt augue interdum velit euismod. Dignissim enim sit amet venenatis urna cursus eget. Consequat mauris nunc congue nisi vitae suscipit tellus. Cursus euismod quis viverra nibh. Platea dictumst vestibulum rhoncus', 'est pellentesque elit ullamcorper dignissim.',]),
                                ('Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Consectetur libero id faucibus nisl tincidunt eget nullam. Sem integer vitae justo eget magna fermentum. Elit duis tristique sollicitudin nibh sit amet commodo. Ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis. Aliquam etiam erat velit scelerisque in dictum non. Posuere ac ut consequat semper viverra nam libero. Egestas diam in arcu cursus euismod. Gravida quis blandit turpis cursus in hac habitasse platea. Vestibulum rhoncus est pellentesque elit ullamcorper. Id eu nisl nunc mi ipsum faucibus. Nullam non nisi est sit amet facilisis magna. Posuere urna nec tincidunt praesent semper feugiat nibh sed. Consequat semper viverra nam libero. Tincidunt eget nullam non nisi est sit amet facilisis.', ['Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Consectetur libero id faucibus nisl tincidunt eget nullam. Sem integer vitae justo eget magna fermentum. Elit duis tristique sollicitudin nibh sit amet commodo. Ullamcorper eget nulla facilisi etiam dignissim diam quis enim lobortis. Aliquam etiam erat velit scelerisque in dictum non. Posuere ac ut consequat semper viverra nam libero. Egestas diam in arcu cursus euismod. Gravida quis blandit turpis cursus in hac habitasse platea. Vestibulum rhoncus est pellentesque elit ullamcorper. Id eu nisl nunc mi ipsum faucibus. Nullam', 'non nisi est sit amet facilisis magna. Posuere urna nec tincidunt praesent semper feugiat nibh sed. Consequat semper viverra nam libero. Tincidunt eget nullam non nisi est sit amet facilisis.',]),
                                ('This is a short test!', ['This is a short test!',]),
                                ('DK3dbQaQwKLPSSAS5QA785SyFpMd7f45thNSbHpbGBgy65diHfiG2yWnNjZn36wBquGw97wYGgR7RPXJWVCHiu84JmewHAr9NMZe0j2i2ycJCqPLKBikQ9yHxvAXEJXLfiRd7G9Hy5BA4LiyhZ1FLVtuyUg0TmCchX9g9GXqcNQN3nAnnV1zUAPY33YNU8CGNdrEEe0UifJy2yLLxS4zU91WrZWhB0tDZXtGWdYD0xuxKWaEpcjLa1FwtDQ2MWijRz5BQvFWveRFPhzeNchkuHHJLnpCtTSVzujDjC7GK1JKF3SZr31mrTjwdxkLCHbGjfqjHfFm4VHi6hZ9R9HLhGccbbSNCpbWmRj2d7Xed5rmh0v5xTZyQ1J7Mk1W7haFihwdJgj15XXf3yEMUnJmuaVuVywXKL9zNGKTEazcFCzit0Z01qFfN4GQc0mQ0MvR3qg1SPMJjGWUvfaT9CdXPffJ5j92xrwZTN4x53nBbHtdcNe1ekGfudUSaRf9ew8DVdUwufvYrpgLjg8pWCzXUayS7iyMS8KQmRYtEferaPGRScVcH1UrbNkQjSUeRLNxFZuVAdF21iNXwG57WQMTPZpuibKfAhHh3RFug7yjpNE8WDiL7N6GYwMBiMFRfS73RZcSf3HYAifqVYTyyc5nmRxFdNhJd456S1zxCwGGDDA9h44xqphTZfkz2j2Z', ['DK3dbQaQwKLPSSAS5QA785SyFpMd7f45thNSbHpbGBgy65diHfiG2yWnNjZn36wBquGw97wYGgR7RPXJWVCHiu84JmewHAr9NMZe0j2i2ycJCqPLKBikQ9yHxvAXEJXLfiRd7G9Hy5BA4LiyhZ1FLVtuyUg0TmCchX9g9GXqcNQN3nAnnV1zUAPY33YNU8CGNdrEEe0UifJy2yLLxS4zU91WrZWhB0tDZXtGWdYD0xuxKWaEpcjLa1FwtDQ2MWijRz5BQvFWveRFPhzeNchkuHHJLnpCtTSVzujDjC7GK1JKF3SZr31mrTjwdxkLCHbGjfqjHfFm4VHi6hZ9R9HLhGccbbSNCpbWmRj2d7Xed5rmh0v5xTZyQ1J7Mk1W7haFihwdJgj15XXf3yEMUnJmuaVuVywXKL9zNGKTEazcFCzit0Z01qFfN4GQc0mQ0MvR3qg1SPMJjGWUvfaT9CdXPffJ5j92xrwZTN4x53nBbHtdcNe1ekGfudUSaRf9ew8DVdUwufvYrpgLjg8pWCzXUayS7iyMS8KQmRYtEferaPGRScVcH1UrbNkQjSUeRLNxFZuVAdF21iNXwG57WQMTPZpuibKfAhHh3RFug7yjpNE8WDiL7N6GYwMBiMFRfS73RZcSf3HYAi', 'fqVYTyyc5nmRxFdNhJd456S1zxCwGGDDA9h44xqphTZfkz2j2Z'])
                            ]
                        )

def test_make_chunks(content, expected_result):
    result=makeChunks(content)

    assert result==expected_result