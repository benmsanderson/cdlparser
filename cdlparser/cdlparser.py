# Ported from Python2 code by Philip A.D. Bentley at https://github.com/rockdoc/cdlparser
"""
A python parser for reading files encoded in netCDF-3 CDL format. The parser is based upon the
flex and yacc files used by the ncgen3 utility that ships with the standard netCDF distribution.

Basic Usage
-----------
The basic usage idiom for parsing CDL text files is as follows:

    myparser = CDL3Parser(...)
    ncdataset = myparser.parse_file(cdlfilename, ...)

If the input CDL file is valid then the above code should result in a netCDF-3 file being generated.
On completion of parsing the output filename can be obtained by querying the myparser.ncfile attribute.

The ncdataset variable returned by the parse_file() method is a handle to a netCDF4.Dataset object,
which you can then query and manipulate as needed. By default this dataset handle is left open when
parsing has completed; hence you will need to call the object's close() method when you're done with
it. If you know that you won't need to manipulate the dataset after parsing then you can set the
close_on_completion keyword argument to True when the parser object is created, thus:

    myparser = CDL3Parser(close_on_completion=True, ...)
    ncdataset = myparser.parse_file(cdlfilename, ...)

By default the name of the netCDF file produced by the parse_file() method is taken from the dataset
name defined in the first line of the CDL file (with a '.nc' extension appended), just as the ncgen
command does. You can supply a different filename, however, via the optional ncfile keyword argument,
e.g.:

    ncdataset = myparser.parse_file(cdlfilename, ncfile="/my/nc/folder/stuff.nc", ...)

In addition to parsing CDL files, you can also parse CDL definitions stored in plain text strings.
The parse_text() method is used in this case, as shown below:

    cdltext = r'netcdf mydataset { dimensions: dim1=10; variables: float var1(dim1); var1:comment="blah blah"; }'
    myparser = CDL3Parser(...)
    ncdataset = myparser.parse_text(cdltext)

The above code should create a netCDF file called 'mydataset.nc' in the current working directory.
Note that the CDL text will usually need to be a raw string of the form r'...' in order for the
string to be passed unmodified to the parser.

You can control the format of the netCDF output file using the 'file_format' keyword argument to the
CDL3Parser constructor. For a description of this and other keyword arguments, read the docstring
for the CDLParser.__init__ method.

Error-handling
--------------
Error-handling is fairly simple in the current version of cdlparser. A CDLSyntaxError exception is
raised if the CDL input source contains syntax errors. If the syntax is fine but there are errors
in the CDL content, then a CDLContentError exception is raised.

The cause of any parsing problems can hopefully be determined by examining the exception text in
combination with any error messages output by the logger object.

Package Dependencies
--------------------
The cdlparser module depends upon the following Python packages. If you don't already have these
then you'll need to download and install them.

* PLY - http://www.dabeaz.com/ply/
* netcdf4-python - http://code.google.com/p/netcdf4-python/
* NumPy - http://numpy.scipy.org/

Creator: Phil Bentley
"""
__version_info__ = (0, 0, 8, 'beta', 0)
__version__ = "%d.%d.%d-%s" % __version_info__[0:4]

import sys, os, logging, types
import ply.lex as lex
from ply.lex import TOKEN
import ply.yacc as yacc
import netCDF4 as nc4
import numpy as np
from functools import reduce

# default fill values for netCDF-3 data types (as defined in netcdf.h include file)
NC_FILL_BYTE   = np.int8(-127)
NC_FILL_CHAR   = np.str_('\0')
NC_FILL_SHORT  = np.int16(-32767)
NC_FILL_INT    = np.int32(-2147483647)
NC_FILL_FLOAT  = np.float32(9.9692099683868690e+36)   # should get rounded to 9.96921e+36
NC_FILL_DOUBLE = np.float64(9.9692099683868690e+36)

# miscellaneous constants as defined in the ncgen3.l file
FILL_STRING = "_"
XDR_INT_MIN = -2147483648
XDR_INT_MAX =  2147483647

# netcdf to numpy data type map
NC_NP_DATA_TYPE_MAP = {
   'byte':    'b',
   'char':    'c',
   'short':   'h',
   'int':     'i',
   'integer': 'i',
   'long':    'i',
   'float':   'f',
   'real':    'f',
   'double':  'd'
}

# default logging options
DEFAULT_LOG_LEVEL  = logging.WARNING
DEFAULT_LOG_FORMAT = "[%(levelname)s] %(funcName)s: %(message)s"

# Exception class for CDL syntax errors
class CDLSyntaxError(Exception) :
   pass

# Exception class for CDL content errors
class CDLContentError(Exception) :
   pass

#---------------------------------------------------------------------------------------------------
class CDLParser(object) :
#---------------------------------------------------------------------------------------------------
   """
   Base class for a CDL lexer/parser that has tokens and rules defined as methods. Client code
   should instantiate concrete subclasses, such as CDL3Parser, rather than this abstract base class.
   """
   tokens = []
   precedence = []

   def __init__(self, close_on_completion=False, file_format='NETCDF3_CLASSIC', log_level=None,
      **kwargs) :
      """
      The currently supported keyword arguments, with their default values, are described below. Any
      other keyword argments are passed through as-is to the PLY parser (via the yacc.yacc function).
      For more information about the latter, visit http://www.dabeaz.com/ply/ply.html
      
      :param close_on_completion: If set to true, the netCDF4.Dataset handle is closed upon completion
         of parsing. By default the handle is left open so that calling code can query and, if
         necessary, manipulate the generated netCDF dataset. In the latter case, the calling code
         is responsible for closing the dataset cleanly using the close() method. [default: False]
      :param file_format: Specifies the netCDF file format to use for the output file generated by
         the parser. The value of this keyword should be one of 'NETCDF3_CLASSIC', 'NETCDF3_64BIT',
         'NETCDF4_CLASSIC' or 'NETCDF4' [default: 'NETCDF3_CLASSIC']
      :param log_level: Sets the logging level to one of the constants defined in the Python logging
         module [default: logging.WARNING]
      """
      self.close_on_completion = close_on_completion
      self.file_format = file_format
      self.log_level = DEFAULT_LOG_LEVEL if log_level is None else log_level
      self.cdlfile = None
      self.ncdataset = None
      #self.dryrun = kwargs.pop('dryrun', False)   # TODO: enable dry-run option
      self.init_logger()

      # Build the lexer and parser
      self.lexer = lex.lex(module=self, debug=kwargs.get('debug', 0))
      self.parser = yacc.yacc(module=self, **kwargs)

   def parse_file(self, cdlfile, ncfile=None) :
      """
      Parse the specified CDL file, writing the output to the netCDF file specified via the
      optional ncfile argument. If that is not specified then the output filename is derived
      from the name specified in the first line of the CDL file (which is the normal behaviour
      of the ncgen command).

      If successful, this method returns an open handle to a netCDF4.Dataset object. Client code
      is responsible for calling the Dataset.close() method when the handle is no longer required.
      Alternatively, this can be done immediately upon completion of parsing by setting the
      close_on_completion keyword argument to True when instantiating the CDLParser instance.

      :param cdlfile: Pathname of the CDL file to parse.
      :param ncfile: Optional pathname of the netCDF file to receive output.
      :returns: A handle to a netCDF4.Dataset object.
      """
      self.cdlfile = cdlfile
      f = open(cdlfile)
      data = f.read()   # FIXME: can we parse input w/o reading entire CDL file into memory?
      f.close()
      return self.parse_text(data, ncfile=ncfile)

   def parse_text(self, cdltext, ncfile=None) :
      """
      Parse the specified CDL text, writing the output to the netCDF file specified via the
      optional ncfile argument. If that is not specified then the output filename is derived
      from the name specified in the first line of the CDL text (which is the normal behaviour
      of the ncgen command).

      If successful, this method returns an open handle to a netCDF4.Dataset object. Client code
      is responsible for calling the Dataset.close() method when the handle is no longer required.
      Alternatively, this can be done immediately upon completion of parsing by setting the
      close_on_completion keyword argument to True when instantiating the CDLParser instance.

      :param cdltext: String containing the CDL text to parse.
      :param ncfile: Optional pathname of the netCDF file to receive output.
      :returns: A handle to a netCDF4.Dataset object.
      """
      self.ncfile = ncfile
      # if netcdf dataset handle exists, e.g. from previous parsing operation, try to close it
      if self.ncdataset :
         try :    self.ncdataset.close()
         except : pass
      self.ncdataset = None
      self.curr_var = None
      self.curr_dim = None
      self.rec_dimname = None
      self.parser.parse(input=cdltext, lexer=self.lexer)
      return self.ncdataset

   def init_logger(self) :
      """Configure a logger object for the parser."""
      console = logging.StreamHandler(stream=sys.stderr)
      console.setLevel(self.log_level)
      fmtr = logging.Formatter(DEFAULT_LOG_FORMAT)
      console.setFormatter(fmtr)
      self.logger = logging.getLogger('cdlparser')
      self.logger.addHandler(console)
      self.logger.setLevel(self.log_level)

#---------------------------------------------------------------------------------------------------
class CDL3Parser(CDLParser) :
#---------------------------------------------------------------------------------------------------
   """
   Class for parsing a CDL file encoded in netCDF-3 classic format. Please refer to this module's
   docstring and also the docstrings in the CDLParser base class for information regarding
   recommended usage patterns.

   All of the tokens making up the CDL3 grammar are encoded as attributes or methods whose names
   begin with 't_'. Similarly, all of the CDL3 parsing rules are encapsulated within methods whose
   names being with 'p_'. These naming conventions are as required by the PLY lexer and parser.
   All t_ and p_ methods should be considered as private to this class. Client code does not need
   to invoke them. In fact the only public methods currently supported are those implemented in the
   CDLParser base class.
   """
   def __init__(self, **kwargs) :
      """
      Construct a CDL3Parser instance. See the CDLParser.__init__ docstring for a description of the
      currently supported keyword arguments.
      """
      super(CDL3Parser, self).__init__(**kwargs)

   # this tells the parser which rule to kick off with (the p_ncdesc method in this case)
   start = "ncdesc"

   # netCDF-3 reserved words - mainly data types
   reserved_words = {
      'byte':    'BYTE_K',
      'char':    'CHAR_K',
      'short':   'SHORT_K',
      'int':     'INT_K',
      'integer': 'INT_K',
      'long':    'INT_K',
      'float':   'FLOAT_K',
      'real':    'FLOAT_K',
      'double':  'DOUBLE_K',
      'unlimited': 'NC_UNLIMITED_K'
   }

   # the full list of CDL tokens to parse - mostly named exactly as per the ncgen.l file
   tokens = [
      'NETCDF', 'DIMENSIONS', 'VARIABLES', 'DATA', 'IDENT', 'TERMSTRING',
      'BYTE_CONST', 'CHAR_CONST', 'SHORT_CONST', 'INT_CONST', 'FLOAT_CONST', 'DOUBLE_CONST',
      'FILLVALUE', 'COMMENT', 'EQUALS', 'LBRACE', 'RBRACE', 'LPAREN', 'RPAREN', 'EOL'
   ] + list(set(reserved_words.values()))

   # literal characters
   literals = [',',':']

   # Partially relaxed version of the UTF8 character set, and the one used in the ncgen3.l flex file.
   UTF8 = r'([\xC0-\xD6][\x80-\xBF])|' + \
          r'([\xE0-\xEF][\x80-\xBF][\x80-\xBF])|' + \
          r'([\xF0-\xF7][\x80-\xBF][\x80-\xBF][\x80-\xBF])'

   # Following comment copied verbatim from ncgen.l file:
   # Don't permit control characters or '/' in names, but other special
   # chars OK if escaped.  Note that to preserve backwards
   # compatibility, none of the characters _.@+- should be escaped, as
   # they were previously permitted in names without escaping.
   idescaped = r"""\\[ !"#$%&'()*,:;<=>?\[\\\]^`{|}~]"""
   ID = r'([a-zA-Z_]|' + UTF8 + r'|\\[0-9])([a-zA-Z0-9_.@+-]|' + UTF8 + r'|'  + idescaped + r')*'

   escaped = r'\\.'
   nonquotes = r'([^"\\]|' + escaped + r')*'
   termstring = r'\"' + nonquotes + r'\"'

   exp = r'([eE][+-]?[0-9]+)'
   float_const  = r'[+-]?[0-9]*\.[0-9]*' + exp + r'?[Ff]|[+-]?[0-9]*' + exp + r'[Ff]'
   double_const = r'[+-]?[0-9]*\.[0-9]*' + exp + r'?[Dd]?|[+-]?[0-9]*' + exp + r'[Dd]?'
   byte_const = r"([+-]?[0-9]+[Bb])|" + \
                r"(\'[^\\]\')|(\'\\.\')|" + \
                r"(\'\\[0-7][0-7]?[0-7]?\')|" + \
                r"(\'\\[xX][0-9a-fA-F][0-9a-fA-F]?\')"

   ### TOKEN DEFINITIONS
   ### Note that the t_xxx naming convention used below is a requirement of the ply package.

   # definitions of simple tokens
   t_EQUALS = r'='
   t_LBRACE = r'\{'
   t_RBRACE = r'\}'
   t_LPAREN = r'\('
   t_RPAREN = r'\)'
   t_EOL    = r';'

   # ignored characters - whitespace, basically
   t_ignore  = ' \r\t\f'

   # opening stanza - pull out the netcdf filename
   def t_NETCDF(self, t) :
      r'(netcdf|NETCDF|netCDF)[ \t]+[^\{]+'
      parts = t.value.split()
      if len(parts) < 2 :
         raise CDLSyntaxError("A netCDF name is required")
      netcdfname = parts[1]
      t.value = deescapify(netcdfname)
      return t

   # main section identifiers
   def t_SECTION(self, t) :
      r'dimensions:|DIMENSIONS:|variables:|VARIABLES:|data:|DATA:'
      t.type = t.value[:-1].upper()
      return t

   # character strings
   @TOKEN(termstring)
   def t_TERMSTRING(self, t) :
      tstring = expand_escapes(t.value)
      i = 0 ; j = len(tstring)
      if tstring[0]  == '"' : i = 1
      if tstring[-1] == '"' : j = -1
      t.value = tstring[i:j]
      #t.value = eval(tstring)
      return t

   # comments
   def t_COMMENT(self, t) :
      r'\/\/.*'
      pass

   # identifier, i.e. a netcdf attribute, dimension or variable name
   @TOKEN(ID)
   def t_IDENT(self, t) :
      if t.value == FILL_STRING :
         t.type = "FILLVALUE"
      elif t.value.lower() in self.reserved_words :
         t.value = t.value.lower()
         t.type = self.reserved_words[t.value]
      else :
         t.type = "IDENT"
      return t

   # numeric constants (order of appearance is extremely important and differs from ncgen3.l file)
   @TOKEN(float_const)
   def t_FLOAT_CONST(self, t) :
      #r'[+-]?[0-9]*\.[0-9]*' + exp + r'?[Ff]|[+-]?[0-9]*' + exp + r'[Ff]'
      try :
         float_val = float(t.value[:-1])
      except :
         errmsg = "Bad float constant: %s" % t.value
         raise CDLContentError(errmsg)
      t.value = np.float32(float_val)
      return t

   @TOKEN(double_const)
   def t_DOUBLE_CONST(self, t) :
      # Original regex in ncgen3.l file. Since the [Ll] suffix is now deprecated, it's not used here.
      #r'[+-]?[0-9]*\.[0-9]*' + exp + r'?[LlDd]?|[+-]?[0-9]*' + exp + r'[LlDd]?'
      try :
         if t.value[-1] in "dD" :
            double_val = float(t.value[:-1])
         else :
            double_val = float(t.value)
      except :
         errmsg = "Bad double constant: %s" % t.value
         raise CDLContentError(errmsg)
      t.value = np.float64(double_val)
      return t

   def t_SHORT_CONST(self, t) :
      r'[+-]?([0-9]+|0[xX][0-9a-fA-F]+)[sS]'
      #r'[+-]?[0-9]+[sS]|0[xX][0-9a-fA-F]+[sS]'   # original regex in ncgen3.l file
      try :
         int_val = int(eval(t.value[:-1]))
      except :
         errmsg = "Bad short constant: %s" % t.value
         raise CDLContentError(errmsg)
      if int_val < -32768 or int_val > 32767 :
         errmsg = "Short constant is outside valid range (-32768 -> 32767): %s" % int_val
         raise CDLContentError(errmsg)
      t.value = np.int16(int_val)
      return t

   @TOKEN(byte_const)
   def t_BYTE_CONST(self, t) :
      #r'[+-]?[0-9]+[Bb]'        # modified regex
      #r'[+-]?[0-9]*[0-9][Bb]'   # original regex in ncgen3.l file
      try :
         if t.value[0] == "'" :
            int_val = ord(eval(t.value))
         else :
            int_val = int(t.value[:-1])
      except :
         errmsg = "Bad byte constant: %s" % t.value
         raise CDLContentError(errmsg)
      if int_val < -128 or int_val > 127 :
         errmsg = "Byte constant outside valid range (-128 -> 127): %s" % int_val
         raise CDLContentError(errmsg)
      t.value = np.int8(int_val)
      return t

   # The following implementation for handling integer constants is a conflation of the separate
   # mechanisms defined in ncgen3.l for decimal, octal and hex integer constants.
   def t_INT_CONST(self, t) :
      r'[+-]?([1-9][0-9]*|0[xX]?[0-9a-fA-F]+|0)'   # [Ll] suffix has been deprecated
      #r'[+-]?([1-9][0-9]*|0)[lL]?' # original regex for decimal integers in ncgen3.l file
      #r'0[xX]?[0-9a-fA-F]+[lL]?'   # original regex for octal or hex integers in ncgen3.l file
      try :
         long_val = int(eval(t.value))
      except :
         errmsg = "Bad integer constant: %s" %  t.value
         raise CDLContentError(errmsg)
      if long_val < XDR_INT_MIN or long_val > XDR_INT_MAX :
         errmsg = "Integer constant outside valid range (%d -> %d): %s" \
            % (XDR_INT_MIN, XDR_INT_MAX, int_val)
         raise CDLContentError(errmsg)
      else :
         t.value = np.int32(long_val)
      return t

   # newlines
   def t_newline(self, t):
      r'\n+'
      t.lexer.lineno += len(t.value)

   def t_error(self, t):
      """Handles token errors."""
      msg  = "Illegal character(s) encountered at line number %d, lexical position %d\n" \
         % (t.lineno, t.lexpos)
      msg += "Token value = '%s'" % t.value
      self.logger.warning(msg)
      t.lexer.skip(1)

   ### PARSING RULES
   ### Note that the p_xxx method-naming convention used below is a requirement of the ply package.
   ### Likewise, the multiline syntax used in several of the method docstrings is also a ply
   ### requirement.

   def p_ncdesc(self, p) :
      """ncdesc : NETCDF init_netcdf LBRACE dimsection vasection datasection RBRACE"""
      if self.ncdataset :
         if self.close_on_completion : self.ncdataset.close()
         self.logger.info("Closed netCDF file " + self.ncfile)
      self.logger.info("Finished parsing")

   def p_init_netcdf(self, p) :
      """init_netcdf :"""
      if not self.ncfile : self.set_filename(p[-1])
      self.ncdataset = nc4.Dataset(self.ncfile, 'w', format=self.file_format)
      self.logger.info("Initialised netCDF file " + self.ncfile)

   def p_dimsection(self, p) :
      """dimsection : DIMENSIONS dimdecls
                    | empty"""

   def p_dimdecls(self, p) :
      """dimdecls : dimdecls dimdecline EOL
                  | dimdecline EOL"""

   def p_dimdecline(self, p) :
      """dimdecline : dimdecline ',' dimdecl
                    | dimdecl"""

   def p_dimdecl(self, p) :
      """dimdecl : dimd EQUALS INT_CONST
                 | dimd EQUALS DOUBLE_CONST
                 | dimd EQUALS NC_UNLIMITED_K"""
      dimname = ""
      if isinstance(p[3], str) :
         if p[3] == "unlimited" :
            if self.rec_dimname :
               raise CDLContentError("Only one UNLIMITED dimension is allowed.")
            dimname = p[1]
            dimlen = 0
            self.rec_dimname = dimname
         else :
            raise CDLContentError("Unrecognised dimension length specifier: '%s'." % p[3])
      else :
         dimname = p[1]
         dimlen = int(p[3])
         if dimlen <= 0 :
            raise CDLContentError("Length of dimension '%s' must be positive." % dimname)
      if dimname :
         self.curr_dim = self.ncdataset.createDimension(dimname, dimlen)
         unlim = " (unlimited)" if dimlen == 0 else ""
         self.logger.info("Created dimension %s with length %s%s" % (dimname, dimlen, unlim))

   def p_dimd(self, p) :
      """dimd : dim"""
      if p[1] in self.ncdataset.dimensions :
         raise CDLContentError("Duplicate declaration for dimension '%s'." % p[1])
      p[0] = p[1]

   def p_dim(self, p) :
      """dim : IDENT"""
      p[0] = p[1]

   def p_vasection(self, p) :
      """vasection : VARIABLES vadecls
                   | gattdecls
                   | empty"""

   def p_vadecls(self, p) :
      """vadecls : vadecls vadecl EOL
                 | vadecl EOL"""

   def p_vadecl(self, p) :
      """vadecl : vardecl
                | attdecl
                | gattdecl"""

   def p_vardecl(self, p) :
      """vardecl : type varlist"""

   def p_varlist(self, p) :
      """varlist : varlist ',' varspec
                 | varspec"""
      if len(p) == 2 :
         p[0] = p[1:]
      else :
         p[0] = p[1] + p[3:]

   def p_varspec(self, p) :
      """varspec : var dimspec"""
      if p[1] in self.ncdataset.variables :
         raise CDLContentError("Duplicate declaration of variable %s." % p[1])
      dims = len(p)==3 and p[2] or ()
      self.curr_var = self.ncdataset.createVariable(p[1], self.datatype, dimensions=dims,
         shuffle=False)
      self.logger.info("Created variable %s with data type '%s' and dimensions %s" \
         % (p[1], self.datatype, dims))

   def p_var(self, p) :
      """var : IDENT"""
      p[0] = p[1]

   def p_dimspec(self, p) :
      """dimspec : LPAREN dimlist RPAREN
                 | empty"""
      if len(p) > 2 : p[0] = p[2]

   def p_dimlist(self, p) :
      """dimlist : dimlist ',' vdim
                 | vdim"""
      #print "dimlist: ", p[:]
      if len(p) == 2 :
         p[0] = p[1:]
      else :
         p[0] = p[1] + p[3:]

   def p_vdim(self, p) :
      """vdim : dim"""
      p[0] = p[1]

   def p_gattdecls(self, p) :
      """gattdecls : gattdecls gattdecl EOL
                   | gattdecl EOL"""

   # Note: in CDL v3, attribute types, whether global or variable scoped, are deduced from the
   # attribute value. They cannot be prefixed with a type declaration, as is possible at CDL v4.
   def p_gattdecl(self, p) :
      """gattdecl : gatt EQUALS attvallist"""
      if self.ncdataset :
         self.set_attribute(':'+p[1], p[3])

   def p_attdecl(self, p) :
      """attdecl : att EQUALS attvallist"""
      if self.ncdataset :
         self.set_attribute(p[1], p[3])

   def p_att(self, p) :
      """att : avar ':' attr"""
      p[0] = p[1] + ':' + p[3]

   def p_gatt(self, p) :
      """gatt : ':' attr"""
      p[0] = p[2]

   def p_avar(self, p) :
      """avar : var"""
      varname = p[1]
      if self.ncdataset :
         if varname not in self.ncdataset.variables :
            raise CDLContentError("Variable %s is not defined or reference precedes definition." \
               % varname)
         self.curr_var = self.ncdataset.variables[varname]
         self.logger.debug("Current variable set to '%s'" % varname)
      p[0] = varname

   def p_attr(self, p) :
      """attr : IDENT"""
      p[0] = p[1]

   def p_attvallist(self, p) :
      """attvallist : attvallist ',' aconst
                    | aconst"""
      #print "attlist:", p[:]
      if len(p) == 2 :
         p[0] = p[1:]
      else :
         p[0] = p[1] + p[3:]

   def p_aconst(self, p) :
      """aconst : attconst"""
      p[0] = p[1]

   def p_attconst(self, p) :
      """attconst : BYTE_CONST
                  | CHAR_CONST
                  | SHORT_CONST
                  | INT_CONST
                  | FLOAT_CONST
                  | DOUBLE_CONST
                  | TERMSTRING"""
      p[0] = p[1]

   def p_datasection(self, p) :
      """datasection : DATA datadecls
                     | DATA
                     | empty"""

   def p_datadecls(self, p) :
      """datadecls : datadecls datadecl EOL
                   | datadecl EOL"""

   def p_datadecl(self, p) :
      """datadecl : avar EQUALS constlist"""
      if self.ncdataset :
         if p[1] not in self.ncdataset.variables :
            raise CDLContentError("Variable %s referenced in data section is not defined." % p[1])
         var = self.ncdataset.variables[p[1]]
         arr = p[3]
         try :
            self.write_var_data(var, arr)
            self.logger.info("Wrote %d data value(s) for variable %s" % (len(arr), p[1]))
         except Exception as exc :
            self.logger.error(str(exc))
            raise

   def p_constlist(self, p) :
      """constlist : constlist ',' dconst
                   | dconst"""
      # FIXME: repeatedly appending values to a list will be inefficient for large data arrays
      if len(p) == 2 :
         p[0] = p[1:]
      else :
         p[0] = p[1] + p[3:]

   def p_dconst(self, p) :
      """dconst : const"""
      p[0] = p[1]

   def p_const(self, p) :
      """const : BYTE_CONST
               | CHAR_CONST
               | SHORT_CONST
               | INT_CONST
               | FLOAT_CONST
               | DOUBLE_CONST
               | TERMSTRING
               | FILLVALUE"""
      # return the value of the constant, or the current variable's fill value if the specified
      # constant value is the string '_'.
      if p[1] == FILL_STRING :
         if self.curr_var is not None and self.curr_var.dtype.kind != 'S' :   # numeric variables only
            if '_FillValue' in self.curr_var.ncattrs() :
               p[0] = self.curr_var._FillValue
            else :
               p[0] = get_default_fill_value(self.curr_var.dtype.char)
         else :
            self.logger.warn("Unable to replace fill value. Check CDL input for possible errors.")
            p[0] = p[1]
      else :
         p[0] = p[1]

   def p_type(self, p) :
      """type : BYTE_K
              | CHAR_K
              | SHORT_K
              | INT_K
              | FLOAT_K
              | DOUBLE_K"""
      # return numpy data type corresponding to netcdf type keyword
      self.datatype = NC_NP_DATA_TYPE_MAP[p[1]]
      p[0] = self.datatype

   def p_empty(self, p) :
      'empty :'
      pass

   def p_error(self, p) :
      """Handles parsing errors."""
      if p :
         errmsg  = "Syntax error at line number %d, lexical position %d\n" % (p.lineno, p.lexpos)
         errmsg += "Token = %s, value = '%s'" % (p.type, p.value)
      else :
         errmsg = "Syntax error: premature EOF encountered."
      self.logger.error(errmsg)
      raise CDLSyntaxError(errmsg)

   ### GENERAL SUPPORT METHODS

   # TODO: consider adding a '_' prefix to these methods to make them pseudo-private.
   def set_filename(self, ncname) :
      """Sets the netCDF filename based on the netCDF name token in the CDL input."""
      if self.cdlfile :
         basedir = os.path.dirname(self.cdlfile)
      else :
         basedir = os.path.abspath(".")
      self.ncfile = os.path.join(basedir, ncname+'.nc')

   def set_attribute(self, attid, attvallist) :
      """Set a global or variable-scope attribute value."""
      if isinstance(attvallist, (list,tuple)) and len(attvallist) == 1 :
         attval = attvallist[0]
      else :
         attval = attvallist
      # global-scope attribute
      if attid[0] == ':' :
         if attid[1:] in self.ncdataset.ncattrs() :
            raise CDLContentError("Duplicate global attribute: %s" % attid)
         self.ncdataset.setncattr(attid[1:], attval)
         self.logger.info("Created global attribute %s = %s" % (attid, repr(attval)))
      # variable-scope attribute
      else :
         try :
            (varname,attname) = attid.split(':')
            var = self.ncdataset.variables[varname]
            if attname in var.ncattrs() :
               raise CDLContentError("Duplicate attribute: %s" % attid)
            if attname == "_FillValue" :
               attval = var.dtype.type(attval)
            var.setncattr(attname, attval)
            self.logger.info("Created attribute %s:%s = %s" % (varname, attname, repr(attval)))
         except :
            raise CDLContentError("Invalid attribute name specification: '%s'" % attid)

   # FIXME: this method is too long - consider refactoring
   def write_var_data(self, var, arr) :
      """Write data array to variable var."""
      self.logger.debug("Scanning data array for variable %s" % var._name)

      # This method needs to take account of the following factors...
      # - whether the variable is scalar or vector
      # - whether the variable is numeric or character-valued
      # - whether the input data array needs padding with fill values
      # - whether the variable is a record variable, i.e. has an unlimited dimension

      is_scalar = (var.ndim == 0)
      is_charvar = (var.dtype.kind == 'S')
      is_recvar = self.rec_dimname in var.dimensions

      # scalar variables ought to be fairly straightforward      
      if is_scalar :
         try :
            var.assignValue(arr[0])
            self.logger.debug("Assigned value %r to scalar variable %s" % (arr[0], var._name))
         except :
            errmsg = "Error attempting to assign data value to scalar variable %s" % var._name
            self.logger.error(errmsg)
            raise CDLContentError(errmsg)
         return

      # determine the expected number of data values for the current variable
      # for char-valued variables we need to divide by the length of the last dimension
      arrlen = len(arr)
      varlen = var.size
      if is_charvar and var.ndim > 0 :
         varlen /= var.shape[-1]
      reclen = 0
      self.logger.debug("Length of passed-in data array = %d" % arrlen)
      if varlen : self.logger.debug("Expected length of variable = %d" % varlen)

      # see if we're dealing with a record variable; if so then work out the record length and, if
      # length of record dimension is 0, assume that total variable length = length of input array
      if is_recvar :
         rec_dimlen = len(self.ncdataset.dimensions[self.rec_dimname])
         if rec_dimlen > 0 :   # record dimension has been set to non-zero
            reclen = varlen / rec_dimlen
         else :                # record dimension is still equal to zero
            varlen = arrlen
            reclen = 1
            if var.ndim > 1 : reclen = reduce(lambda x,y: x*y, [x for x in var.shape if x > 0])
            self.logger.debug("Expected length of variable = %d" % varlen)
         # check that reclen is integer factor of variable length
         if varlen % reclen != 0 :
            errmsg = "Record length %d is not a factor of variable length %d" % (reclen, varlen)
            raise CDLContentError(errmsg)
         self.logger.debug("Length of one data record = %d" % reclen)

      # pad out data array with fill values if too few values were defined in the CDL source
      if arrlen < varlen :
         pad_array(var, varlen, arr)
         self.logger.info("Padded input data array with %d fill values" % (varlen-arrlen))
         arrlen = len(arr)

      # convert input data to suitably shaped numpy array
      try :
         if is_charvar :
            put_char_data(var, arr, reclen)
         else :
            put_numeric_data(var, arr, reclen)
      except Exception as exc :
         errmsg = "Error attempting to write data array for variable %s\n" % var._name
         errmsg += "Exception details are as follows:\n%s" % str(exc)
         raise CDLContentError(errmsg)

   def _lextest(self, data) :
      """private method - for test purposes only"""
      self.lexer.input(data)
      print("-----")
      while 1 :
         t = self.lexer.token()
         if not t : break
         print("type: %-15s\tvalue: %s" % (t.type, t.value))
      print("-----")

#---------------------------------------------------------------------------------------------------
def put_numeric_data(var, arr, reclen=0) :
#---------------------------------------------------------------------------------------------------
   """Write numeric data array to netcdf variable."""
   nparr = np.array(arr, dtype=var.dtype)
   shape = list(var.shape)
   if reclen : shape[0] = len(arr) / reclen
   nparr.shape = shape
   var[:] = nparr

#---------------------------------------------------------------------------------------------------
def put_char_data(var, arr, reclen=0) :
#---------------------------------------------------------------------------------------------------
   """Write character data array to netcdf variable."""
   maxlen = var.shape[-1] if var.ndim > 0 else 1
   nparr = str_list_to_char_arr(arr, maxlen)
   shape = list(var.shape)
   if reclen : shape[0] = len(arr) / reclen
   nparr.shape = shape
   var[:] = nparr

#---------------------------------------------------------------------------------------------------
def str_list_to_char_arr(slist, maxlen) :
#---------------------------------------------------------------------------------------------------
   """
   Convert a list of regular python strings to a numpy character array of type '|S1', which is what
   is required by the netCDF4 module. The maximum length of each string in the output netcdf array
   is defined by maxlen. It's usually the last dimension in the variable declaration.
   """
   stype = 'S%d' % maxlen
   tarr = np.array(slist, dtype=stype)
   return nc4.stringtochar(tarr)

#---------------------------------------------------------------------------------------------------
def pad_array(var, varlen, arr) :
#---------------------------------------------------------------------------------------------------
   """
   Pad out array arr with fill values if it contains fewer elements than are required by the host
   variable.
   """
   if '_FillValue' in var.ncattrs() :
      fv = var._FillValue
   elif 'missing_value' in var.ncattrs() :
      fv = var.missing_value
   else :
      fv = get_default_fill_value(var.dtype.char)
   arrlen = len(arr)
   arr.extend([fv]*(varlen-arrlen))

#---------------------------------------------------------------------------------------------------
def deescapify(name) :
#---------------------------------------------------------------------------------------------------
   """
   A Python version of ncgen's deescapify() function (see genlib.c). The code here is a fairly
   literal translation of that function. I expect this could be recoded in a more pythonic way
   using, say, regular expressions.
   """
   if '\\' not in name : return name
   newname = ''
   i = 0
   # delete '\' chars, except change '\\' to '\'
   while i < len(name) :
      if name[i] == '\\' :
         if name[i+1] == '\\' :
            newname += '\\'
            i += 1
      else :
         newname += name[i]
      i += 1
   return newname

def expand_escapes(s, encoding='utf-8'):
    return (s.encode('latin1')         # To bytes, required by 'unicode-escape'
             .decode('unicode-escape') # Perform the actual octal-escaping decode
             .encode('latin1')         # 1:1 mapping back to bytes
             .decode(encoding))        # Decode original encoding


#---------------------------------------------------------------------------------------------------
def get_default_fill_value(datatype) :
#---------------------------------------------------------------------------------------------------
   """Returns the default netCDF fill value for the specified numpy dtype.char code."""
   if datatype == 'b' :
      return NC_FILL_BYTE
   elif datatype in ('S','U') :
      return NC_FILL_CHAR
   elif datatype in ('h','s') :
      return NC_FILL_SHORT
   elif datatype == 'i' :
      return NC_FILL_INT
   elif datatype == 'f' :
      return NC_FILL_FLOAT
   elif datatype == 'd' :
      return NC_FILL_DOUBLE
   else :
      raise CDLContentError("Unrecognised data type '%s'" % datatype)

#---------------------------------------------------------------------------------------------------
def main() :
#---------------------------------------------------------------------------------------------------
   """Rudimentary main function - primarily for testing purposes at this point in time."""
   debug = 0
   if len(sys.argv) < 2 :
      print("usage: python cdlparser.py cdlfile [keyword=value, ...]")
      sys.exit(1)
   cdlfile = sys.argv[1]
   kwargs = {}
   if len(sys.argv) > 2 :
      keys = [x.split('=')[0] for x in sys.argv[2:]]
      vals = [eval(x.split('=')[1]) for x in sys.argv[2:]]
      kwargs = dict(list(zip(keys,vals)))
   cdlparser = CDL3Parser(**kwargs)
   ncdataset = cdlparser.parse_file(cdlfile)
   try :
      ncdataset.close()   # wrap in try block since dataset may get closed by parser
   except :
      pass

#---------------------------------------------------------------------------------------------------
if __name__ == '__main__':
#---------------------------------------------------------------------------------------------------
   main()
