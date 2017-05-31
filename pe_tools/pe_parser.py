import struct, io
from .blob import Blob, IoBlob, PadBlob, write_blob, join_blobs
from .structs2 import Struct

_IMAGE_FILE_HEADER = Struct(
    'H:Machine',
    'H:NumberOfSections',
    'I:TimeDateStamp',
    'I:PointerToSymbolTable',
    'I:NumberOfSymbols',
    'H:SizeOfOptionalHeader',
    'H:Characteristics',
    )

IMAGE_NT_OPTIONAL_HDR32_MAGIC = 0x10b
IMAGE_NT_OPTIONAL_HDR64_MAGIC = 0x20b

_IMAGE_OPTIONAL_HEADER32 = Struct(
    'B:MajorLinkerVersion',
    'B:MinorLinkerVersion',
    'I:SizeOfCode',
    'I:SizeOfInitializedData',
    'I:SizeOfUninitializedData',
    'I:AddressOfEntryPoint',
    'I:BaseOfCode',
    'I:BaseOfData',
    'I:ImageBase',
    'I:SectionAlignment',
    'I:FileAlignment',
    'H:MajorOperatingSystemVersion',
    'H:MinorOperatingSystemVersion',
    'H:MajorImageVersion',
    'H:MinorImageVersion',
    'H:MajorSubsystemVersion',
    'H:MinorSubsystemVersion',
    'I:Reserved1',
    'I:SizeOfImage',
    'I:SizeOfHeaders',
    'I:CheckSum',
    'H:Subsystem',
    'H:DllCharacteristics',
    'I:SizeOfStackReserve',
    'I:SizeOfStackCommit',
    'I:SizeOfHeapReserve',
    'I:SizeOfHeapCommit',
    'I:LoaderFlags',
    'I:NumberOfRvaAndSizes',
    )

_IMAGE_OPTIONAL_HEADER64 = Struct(
    'B:MajorLinkerVersion',
    'B:MinorLinkerVersion',
    'I:SizeOfCode',
    'I:SizeOfInitializedData',
    'I:SizeOfUninitializedData',
    'I:AddressOfEntryPoint',
    'I:BaseOfCode',
    'Q:ImageBase',
    'I:SectionAlignment',
    'I:FileAlignment',
    'H:MajorOperatingSystemVersion',
    'H:MinorOperatingSystemVersion',
    'H:MajorImageVersion',
    'H:MinorImageVersion',
    'H:MajorSubsystemVersion',
    'H:MinorSubsystemVersion',
    'I:Reserved1',
    'I:SizeOfImage',
    'I:SizeOfHeaders',
    'I:CheckSum',
    'H:Subsystem',
    'H:DllCharacteristics',
    'Q:SizeOfStackReserve',
    'Q:SizeOfStackCommit',
    'Q:SizeOfHeapReserve',
    'Q:SizeOfHeapCommit',
    'I:LoaderFlags',
    'I:NumberOfRvaAndSizes',
    )

_IMAGE_DATA_DIRECTORY = Struct(
    'I:VirtualAddress',
    'I:Size',
    )

_IMAGE_SECTION_HEADER = Struct(
    '8s:Name',
    'I:PhysicalAddress:VirtualSize',
    'I:VirtualAddress',
    'I:SizeOfRawData',
    'I:PointerToRawData',
    'I:PointerToRelocations',
    'I:PointerToLinenumbers',
    'H:NumberOfRelocations',
    'H:NumberOfLinenumbers',
    'I:Characteristics',
    )

def _align(offs, alignment):
    return (offs + alignment - 1) // alignment * alignment

IMAGE_DIRECTORY_ENTRY_EXPORT = 0
IMAGE_DIRECTORY_ENTRY_IMPORT = 1
IMAGE_DIRECTORY_ENTRY_RESOURCE = 2
IMAGE_DIRECTORY_ENTRY_EXCEPTION = 3
IMAGE_DIRECTORY_ENTRY_SECURITY = 4
IMAGE_DIRECTORY_ENTRY_BASERELOC = 5
IMAGE_DIRECTORY_ENTRY_DEBUG = 6
IMAGE_DIRECTORY_ENTRY_ARCHITECTURE = 7
IMAGE_DIRECTORY_ENTRY_GLOBALPTR = 8
IMAGE_DIRECTORY_ENTRY_TLS = 9
IMAGE_DIRECTORY_ENTRY_LOAD_CONFIG = 10
IMAGE_DIRECTORY_ENTRY_BOUND_IMPORT = 11
IMAGE_DIRECTORY_ENTRY_IAT = 12
IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT = 13
IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR = 14

class _PeSection:
    def __init__(self, hdr, data):
        self.hdr = hdr
        self.data = data

def pe_checksum(*blobs):
    blob = join_blobs(blobs)
    total_len = len(blob)

    r = 0
    while len(blob) >= 0x1000:
        words = struct.unpack('<2048H', bytes(blob[:0x1000]))
        r += sum(words)
        blob = blob[0x1000:]

    words = struct.unpack('<' + 'H'*(len(blob) // 2), bytes(blob))
    r += sum(words)

    while r > 0xffff:
        c = r
        r = 0
        while c:
            r += c & 0xffff
            c >>= 16

    return r + total_len

class _PeFile:
    def __init__(self, blob):
        pe_offs, = blob.load(0x3c, '<H')

        fin = blob.seek(pe_offs)

        sig = fin.read(4)
        if sig != b'PE\0\0':
            raise RuntimeError('Not a PE file: PE signature is missing.')

        hdr = _IMAGE_FILE_HEADER.parse(fin)
        opt_sig, = struct.unpack('<H', fin.read(2))
        if opt_sig == IMAGE_NT_OPTIONAL_HDR32_MAGIC:
            opt = _IMAGE_OPTIONAL_HEADER32.parse(fin)
            opt.sig = opt_sig
        elif opt_sig == IMAGE_NT_OPTIONAL_HDR64_MAGIC:
            opt = _IMAGE_OPTIONAL_HEADER64.parse(fin)
            opt.sig = opt_sig
        else:
            raise RuntimeError('Unknown optional header type.')

        self._checksum_offs = pe_offs + 4 + _IMAGE_FILE_HEADER.size + 4*16

        if opt.CheckSum != 0:
            real_checksum = pe_checksum(blob[:self._checksum_offs], b'\0\0\0\0', blob[self._checksum_offs + 4:])
            if opt.CheckSum != real_checksum:
                raise RuntimeError('incorrect checksum')

        if opt.FileAlignment == 0:
            raise RuntimeError('IMAGE_OPTIONAL_HEADER.FileAlignment must be nonzero')

        dds = [_IMAGE_DATA_DIRECTORY.parse(fin) for dd_idx in range(opt.NumberOfRvaAndSizes)]

        def make_pe_section(idx, hdr):
            name = hdr.Name.rstrip(b'\0')

            if hdr.PointerToRawData % opt.FileAlignment != 0:
                raise RuntimeError('Section {}@{} is misaligned ({})'.format(name, idx, hdr.PointerToRawData))
            if hdr.SizeOfRawData % opt.FileAlignment != 0:
                raise RuntimeError('Size of section {}@{} is misaligned ({})'.format(name, idx, hdr.SizeOfRawData))

            if hdr.PointerToRawData == 0:
                data = None
            else:
                data = blob[hdr.PointerToRawData:hdr.PointerToRawData + hdr.SizeOfRawData]

            return _PeSection(hdr, data)

        sections = [make_pe_section(sec_idx, _IMAGE_SECTION_HEADER.parse(fin)) for sec_idx in range(hdr.NumberOfSections)]

        present_secs = sorted((sec for sec in sections if sec.hdr.SizeOfRawData != 0), key=lambda sec: sec.hdr.PointerToRawData)
        if not present_secs:
            raise RuntimeError('no present sections')

        i = 1
        while i < len(present_secs):
            if present_secs[i-1].hdr.PointerToRawData + present_secs[i-1].hdr.SizeOfRawData != present_secs[i].hdr.PointerToRawData:
                raise RuntimeError('there are holes between sections')
            i += 1

        last_sec = present_secs[-1]
        end_of_image = last_sec.hdr.PointerToRawData + last_sec.hdr.SizeOfRawData

        self._blob = blob
        self._dos_stub = blob[:pe_offs]
        self._file_header = hdr
        self._opt_header = opt
        self._data_directories = dds
        self._sections = sections

        self._trailer_offset = end_of_image
        self._trailer = blob[end_of_image:]

        self._check_vm_overlaps()

    def _file_align(self, addr):
        return _align(addr, self._opt_header.FileAlignment)

    def _mem_align(self, addr):
        return _align(addr, self._opt_header.SectionAlignment)

    def _check_vm_overlaps(self):
        prev = None
        for sec in sorted(self._sections, key=lambda sec: sec.hdr.VirtualAddress):
            if sec.hdr.VirtualAddress % self._opt_header.SectionAlignment != 0:
                raise RuntimeError('sections are misaligned in memory')

            if prev and prev.hdr.VirtualAddress + prev.hdr.VirtualSize > sec.hdr.VirtualAddress:
                raise RuntimeError('sections overlap in memory')
            prev = sec

    def get_vm(self, start, stop):
        for sec in self._sections:
            if sec.hdr.VirtualAddress <= start and sec.hdr.VirtualAddress + sec.hdr.VirtualSize >= stop:
                sec_offs = start - sec.hdr.VirtualAddress
                init_size = min(sec.hdr.SizeOfRawData - sec_offs, stop - start)
                uninit_size = stop - start - init_size

                return sec.data[sec_offs:sec_offs + init_size] + PadBlob(uninit_size)

    def has_trailer(self):
        return bool(self._trailer)

    def remove_trailer(self):
        self.remove_signature()
        self._trailer = b''

    def has_signature(self):
        return len(self._data_directories) > IMAGE_DIRECTORY_ENTRY_SECURITY and self._data_directories[IMAGE_DIRECTORY_ENTRY_SECURITY].VirtualAddress != 0

    def remove_signature(self):
        if len(self._data_directories) < IMAGE_DIRECTORY_ENTRY_SECURITY:
            return

        dd = self._data_directories[IMAGE_DIRECTORY_ENTRY_SECURITY]
        if dd.Size == 0:
            return

        if dd.VirtualAddress + dd.Size != self._trailer_offset + len(self._trailer):
            raise RuntimeError('signature is not at the end of the file')

        if dd.VirtualAddress < self._trailer_offset:
            raise RuntimeError('signature is not contained in the pe trailer')

        self._trailer = self._trailer[:-dd.Size]
        dd.VirtualAddress = 0
        dd.Size = 0

    def has_directory(self, idx):
        if len(self._data_directories) < idx:
            return False

        dd = self._data_directories[idx]
        return dd.VirtualAddress != 0

    def find_directory(self, idx):
        if len(self._data_directories) < idx:
            return None

        dd = self._data_directories[idx]
        if dd.VirtualAddress == 0:
            return None

        return slice(dd.VirtualAddress, dd.VirtualAddress + dd.Size)

    def _get_directory_section(self, start, stop):
        for sec_idx, sec in enumerate(self._sections):
            vm_start = sec.hdr.VirtualAddress
            vm_stop = vm_start + sec.hdr.VirtualSize
            if vm_start == start and vm_stop >= stop and vm_stop == stop:
                return sec_idx

    def _find_vm_hole(self, secs, size):
        sorted_secs = sorted(secs, key=lambda sec: sec.VirtualAddress)
        i = 1
        while i < len(sorted_secs):
            start = self._mem_align(sorted_secs[i-1].VirtualAddress + sorted_secs[i-1].VirtualSize)
            stop = sorted_secs[i].VirtualAddress

            if stop - start >= size:
                return slice(start, self._mem_align(start + size))

            i += 1

        start = self._mem_align(sorted_secs[-1].VirtualAddress + sorted_secs[-1].VirtualSize)
        return slice(start, self._mem_align(start + size))

    def _resize_directory(self, idx, size):
        dd = self._data_directories[idx]

        sec_idx = self._get_directory_section(dd.VirtualAddress, dd.VirtualAddress + dd.Size)
        if sec_idx is None:
            raise RuntimeError('can\'t modify a directory that is not associated with a section')

        sec = self._sections[sec_idx]
        if sec.hdr.VirtualSize >= size:
            sec.hdr.VirtualSize = size
            dd.Size = size
            return sec_idx, slice(sec.hdr.VirtualAddress, sec.hdr.VirtualAddress + sec.hdr.VirtualSize)

        other_secs = [sec for idx, sec in enumerate(self._sections) if idx != sec_idx]
        sl = self._find_vm_hole(other_secs, size)

        sec.VirtualAddress = sl.start
        sec.VirtualSize = sl.stop

        dd = self._data_directories[idx]
        dd.VirtualAddress = sl.start
        dd.Size = size

        return sec_idx, sl

    def resize_directory(self, idx, size):
        sec_idx, sl = self._resize_directory(idx, size)
        return sl

    def set_directory(self, idx, blob):
        sec_idx, sl = self._resize_directory(idx, len(blob))

        sec = self._sections[sec_idx]
        sec.data = blob

    def store(self, fout):
        self._check_vm_overlaps()

        header_end = (len(self._dos_stub) + 4 + self._file_header.size + 2 + self._opt_header.size
            + len(self._data_directories) * _IMAGE_DATA_DIRECTORY.size + len(self._sections) * _IMAGE_SECTION_HEADER.size)
        section_offset = self._file_align(header_end)
        header_pad = section_offset - header_end

        for sec in self._sections:
            if sec.hdr.PointerToRawData == 0:
                continue
            sec.hdr.PointerToRawData = section_offset
            sec.hdr.SizeOfRawData = self._file_align(len(sec.data))
            section_offset = section_offset + sec.hdr.SizeOfRawData

        self._opt_header.CheckSum = 0

        new_file = []

        new_file.append(self._dos_stub)
        new_file.append(b'PE\0\0')
        new_file.append(self._file_header.pack())
        new_file.append(struct.pack('<H', self._opt_header.sig))
        new_file.append(self._opt_header.pack())

        for dd in self._data_directories:
            new_file.append(dd.pack())

        for sec in self._sections:
            new_file.append(sec.hdr.pack())

        new_file.append(PadBlob(header_pad))
        for sec in self._sections:
            new_file.append(sec.data)
            with_pad = self._file_align(len(sec.data))
            pad = with_pad - len(sec.data)
            if pad:
                new_file.append(PadBlob(pad))

        new_file.append(self._trailer)

        out_blob = join_blobs(new_file)
        new_checksum = pe_checksum(out_blob)

        out_blob = out_blob[:self._checksum_offs] + struct.pack('<I', new_checksum) + out_blob[self._checksum_offs + 4:]

        write_blob(fout, out_blob)

def parse_pe(blob):
    if not isinstance(blob, Blob):
        blob = IoBlob(blob)
    return _PeFile(blob)