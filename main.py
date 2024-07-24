# standard library
import sys
from argparse import Namespace, ArgumentParser
from sys import stderr
import os
import shutil
# from pprint import pprint
from time import sleep
from multiprocessing import cpu_count
from typing import Iterable
from enum import Enum

# must download
import pyperclip as pc
import validators
from pytube import YouTube, Stream, StreamQuery, Playlist
from pytube.exceptions import MembersOnly, AgeRestrictedError, RegexMatchError
from moviepy.editor import AudioFileClip, VideoFileClip
from tqdm import tqdm
import requests

# pip only
import eyed3
import eyed3.id3.frames

class InvalidFormatException(Exception):
	pass

class Format(Enum):
	Audio = 'audio'
	Video = 'video'
	Both = 'both'

	@staticmethod
	def to_format(s: str):
		d = dict()
		for format in Format:
			d[format.name.lower()] = format
		
		if s in d:
			return d[s]
		else:
			raise InvalidFormatException(f'Invalid format: {s}\nAvailable types: {list(map(lambda x: x.name, Format))}')

def get_args() -> Namespace:
	parser = ArgumentParser()

	types = list(map(lambda x: x.name.lower(), Format))
	group = parser.add_mutually_exclusive_group(required=True)
	group.add_argument('-u', '--url', nargs='+', help='URL of a video')
	group.add_argument('-p', '--playlist', help='URL of a playlist')
	group.add_argument('-c', '--clipboard', dest='clipboard', action='store_true', help='Extract URL from clipboard.')
	group.set_defaults(clipboard=False)

	parser.add_argument('-f', '--format', type=Format.to_format, default=Format.Audio, help=f'{"/".join(types)} - default: audio')
	parser.add_argument('-m', '--max_resolution', type=int, default=1080, help='Maximal resolution to download. Used when \'-p\' flag is NOT used.')

	#parser.add_argument('-p', '--progressive', dest='progressive', action='store_true', help='Download video and audio together (max 720p, but faster)')
	parser.set_defaults(progressive=True)
	#parser.add_argument('-p', '--progressive', type=bool, default=False, nargs='?', const=True, help='Download video and audio together (max 720p, but faster)')
	parser.add_argument('-d', '--development', dest='development', action='store_true', help='Enable development features.')
	parser.set_defaults(development=False)

	parser.add_argument('--single-progress-bar', dest='single_progress_bar', action='store_true', help='Only show one progress bar.')
	parser.set_defaults(single_progress_bar=False)
	#parser.add_argument('-d', '--development', type=bool, default=False, nargs='?', const=True, help='Enable development features.')

	parser.add_argument('-t', '--threads', default=cpu_count() // 2, type=int, help=f'When using advanced video download, amount of threads to use for encoding the final video. Default: {cpu_count() // 2}')
	parser.add_argument('--compress_level', type=int, default=5, help='Compression level [0-9]. Higher level of compression means it will take longer. Default: 5')

	parser.add_argument('--exclude_metadata', dest='add_metadata', action='store_false', help='Exclude metadata (audio only).')
	parser.set_defaults(add_metadata=True)

	parser.add_argument('--artist', type=str, default=None, help='Add artist metadata')
	parser.add_argument('--album', type=str, default=None, help='Add album metadata')
	parser.add_argument('--title', type=str, default=None, help='Add song title metadata')
	parser.add_argument('-s', '--silent', dest='silent', action='store_true')
	parser.set_defaults(silent=False)
	#parser.add_argument('-s', '--silent', type=bool, default=False, nargs='?', const=True)

	args = parser.parse_args()
	
	if not (0 <= args.compress_level and args.compress_level <= 9):
		print(f'Invalid compress level ({args.compress_level}). Use --help for more information.', file=stderr)
		exit(1)
	
	if args.clipboard:
		url_from_clipboard = pc.paste()
		if validators.url(url_from_clipboard):
			setattr(args, 'url', [url_from_clipboard])
		else:
			print(f'URL from clipboard is not valid: "{url_from_clipboard}"')
			exit(1)
	
	return args

def get_metadata(yt: YouTube) -> dict:
	return yt.metadata.metadata[0] if len(yt.metadata.metadata) > 0 else dict()

def download_video_part(streams: StreamQuery, max_resolution: int, verbose: bool) -> str:
	max_suitable_resolution = max(
		filter(
			lambda res: res <= max_resolution,
			map(
				lambda s: int(s.resolution.strip('p')),
				streams
			)
		)
	)

	#pprint(streams)

	best_video_streams: list[Stream] = list(
		filter(
			lambda s: int(s.resolution.strip('p')) <= max_suitable_resolution,
			streams
		)
	)

	# print('Best video streams:')
	# pprint(best_video_streams)

	best_video_stream: Stream = best_video_streams[-1]
	if verbose:
		print(f'Downloading video part in resolution {best_video_stream.resolution} in {best_video_stream.fps} fps ...')
	res = best_video_stream.download()
	return res

def download_audio_part(streams: StreamQuery, verbose: bool) -> str:
	
	stream: Stream = streams.order_by('abr').desc().first()

	if verbose:
		print('Downloading audio part...')
	res = stream.download()
	return res

def get_compression_preset(compression_level: int) -> str:
	presets = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast',
		'medium', 'slow', 'slower', 'veryslow', 'placebo']
	return presets[compression_level]

def remove_forbidden(s: str) -> str:
	forbidden_symbols = list()
	if sys.platform == 'win32':
		forbidden_symbols = ['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>']
	
	for symbol in forbidden_symbols:
		s = s.replace(symbol, '_')
	
	s = ''.join(map(lambda symbol: symbol if ord(symbol) <= 127 else '_', s))
	
	return s

def on_progress_callback(_, chunk: bytes, bytes_remaining: int, progress_bar: tqdm):
	progress_bar.update(len(chunk))

def download_audio(args: Namespace, yt: YouTube, all_streams, prefix: str = "", verbose: bool = True):
	'''
	Download audio according to the arguments.
	'''
	# stream = all_streams.filter(type='audio', subtype='webm').order_by('abr').last()
	stream = all_streams.filter(type='audio').order_by('abr').last()
	stream: Stream

	if verbose:
		print(f'Downloading audio for {yt.title} in {stream.abr}')
	#out_base = yt.title.replace(" ","_")
	out_base = remove_forbidden(yt.title)
	out_ext = stream.mime_type.split("/")[1]
	out_filename = f'{out_base}.{out_ext}'
	out_final = f'{prefix}{"-" if len(prefix) > 0 else ""}{out_base}.mp3'
	#out_filename = remove_forbidden(out_filename) 
	if out_final not in os.listdir():
		stream.download(filename=out_filename)
		
		# fix file metadata
		with AudioFileClip(out_filename) as audio_clip:
			loggerType = 'bar' if verbose else None
			# remove "bps" from "160kbps" for ffmpeg
			bitrate = None # '256k' # f'{stream.abr[:-3]}'
			audio_clip.write_audiofile(out_final, nbytes=4, bitrate=bitrate, logger=loggerType)
			#audio_clip.write_audiofile(out_final, logger=None)
		os.remove(out_filename)

	if (args.add_metadata or args.artist 
		or args.title or args.album):
		yt_metadata = get_metadata(yt)

		if args.artist is not None:
			yt_metadata['Artist'] = args.artist
		if args.title is not None:
			yt_metadata['Song'] = args.title
		if args.title is not None:
			yt_metadata['Album'] = args.album
		if verbose:
			print('Metadata:')
			for key in yt_metadata:
				print(f'{key}: {yt_metadata[key]}')

		if len(yt_metadata) == 0:
			if verbose:
				print('No metadata found.', file=stderr)

		audio_file = eyed3.load(out_final)

		audio_file.initTag(version=(2, 3, 0))  # version is important for thumbnail

		tag: eyed3.core.Tag = audio_file.tag

		if 'Artist' in yt_metadata:
			tag.artist = yt_metadata['Artist']
		else:
			tag.artist = yt.author

		if 'Song' in yt_metadata:
			tag.title = yt_metadata['Song']
		else:
			tag.title = yt.title

		if 'Album' in yt_metadata:
			tag.album = yt_metadata['Album']
		else:
			tag.album = yt.title

		if verbose:
			print('Downloading thumbnail...')
		resp = requests.get(yt.thumbnail_url, stream=True)
		if resp.status_code == 200:
			#print(resp.headers)
			#tag.images.set(eyed3.id3.frames.ImageFrame.FRONT_COVER, resp.content, 'image/jpeg')
			cover_img = resp.content
			# with open(f'{ remove_forbidden(yt.title) }_cover.{ resp.headers["Content-Type"].split("/")[-1] }', 'bw') as cover_file:
			# 	cover_file.write(cover_img)
			tag.images.set(3, cover_img, 'image/jpg')
			if verbose:
				print('Thumbnail has been set.')

		tag.save()

def download_video(args: Namespace, yt: YouTube, all_streams: StreamQuery, verbose: bool):
	'''
	Download video according to the arguments.
	'''
	if args.development:
		if not args.development: # or len(videos) > 1:
			print('This part is currently under development. Please, use flag -p to download a single file with both audio and video (upto 720p).', file=stderr)
			#print('Failed to download.', file=stderr)
			exit(1)

		current_process_id = f'{os.getpid()}_{remove_forbidden(yt.title)}'
		if verbose:
			print(f'Creating temporary directory: {current_process_id}')

		# save to temporary directory
		os.mkdir(str(current_process_id))
		os.chdir(str(current_process_id))
		try:
			audio_file_path = download_audio_part(all_streams.filter(type='audio'), verbose)
			video_file_path = download_video_part(all_streams.filter(type='video', subtype='mp4').order_by('resolution'), args.max_resolution, verbose)

			title = remove_forbidden(yt.title)
			final_video_filename = f'{title}.mp4'
			if verbose:
				print('Merging video and audio...')
			with VideoFileClip(video_file_path) as video_clip:
				with AudioFileClip(audio_file_path) as audio_clip:
					with video_clip.set_audio(audio_clip) as final_clip:
						final_clip: VideoFileClip
						compression_preset = get_compression_preset(args.compress_level)
						final_clip.write_videofile(final_video_filename, 
							#codec="h264_nvenc",
							threads=args.threads, preset=compression_preset, logger='bar' if verbose else None)

			
			shutil.move(final_video_filename, os.pardir)
			if verbose:
				print('Done.')
		except KeyboardInterrupt:
			print('\nDownload has been canceled.', file=stderr)
		# except Exception as e:
		# 	print(f'The following error occurred:\n{e}', file=stderr)
		finally:
			#sleep(1)
			os.chdir('..')
			#shutil.rmtree( str(current_process_id) )

	# progressive -> video and audio are in one file together (max 720p)
	else:
		best_stream: Stream = all_streams.filter(type='video', progressive=True, subtype='mp4').order_by('resolution').last()
		filename = remove_forbidden(yt.title + '.' + best_stream.mime_type.split('/')[1])
		if filename in os.listdir():
			return
		if verbose:
			print(f'Downloading {yt.title}...', file=stderr)
		best_stream.download(filename=filename)

def get_urls(args: Namespace) -> list[YouTube]:
	videos: list[YouTube]
	if args.playlist is not None:
		try:
			playlist = Playlist(args.playlist)
			print(f'Downloading playlist: {playlist.title}', file=stderr)
			dir_name = f'playlist-{ remove_forbidden( playlist.title.replace(" ", "_") ) }'

			if os.path.exists(dir_name):
				os.chdir(dir_name)
				if len(os.listdir()) > 0:
					print(f'Directory "{dir_name}" already exists.')
					resp = input('Do you want to remove its contents? [y/n]\n').lower()
					if resp == 'y':
						for file in os.listdir():
							os.remove(file)
				# else:
				# 	print('Leaving files in the directory.')
			else:
				os.mkdir(dir_name)
				os.chdir(dir_name)

			videos = list(playlist.videos)
		except:
			print(f'Failed to download media from {args.playlist}', file=stderr)
			exit(1)
	if args.url:
		try:
			videos = [ YouTube(url) for url in args.url ]
		except:
			print(f'Failed to download media from {args.url}', file=stderr)
			exit(1)
	return videos

def progress_update(iterator: Iterable, yt: YouTube):
	iterator.desc = f'{yt.title[:35]}'
	iterator.refresh()

def main():
	args = get_args()

	videos: list[YouTube] = get_urls(args)

	verbose_cond = args.playlist is None
	verbose: bool = not args.silent and verbose_cond and not args.single_progress_bar
	iterator: Iterable = tqdm(enumerate(videos), ascii=True, total=len(videos)) if not verbose_cond else enumerate(videos)
	try:
		for i, yt in iterator:
			yt: YouTube
			
			if not verbose_cond:
				progress_update(iterator, yt)

			try:
				all_streams = yt.streams
			except MembersOnly: # video in the playlist is only for members of the channel
				continue
			except AgeRestrictedError:
				continue
			except RegexMatchError:
				continue

			if args.format in [Format.Video, Format.Both]:
				download_video(args, yt, all_streams, verbose)
			if args.format in [Format.Audio, Format.Both]:
				prefix = "" if args.playlist is None else f"{i:03d}"
				download_audio(
					args,
					yt,
					all_streams,
					prefix,
					verbose)
		# if len(videos) > 1:
		# 	for obj in os.listdir():
		# 		if os.path.isdir(obj) and obj.startswith(f'{os.getpid()}'):
		# 			shutil.rmtree(obj)
	except KeyboardInterrupt:
		print('Your download has been canceled.')
		exit(1)

if __name__ == '__main__':
	main()